#define _GNU_SOURCE

#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

#define MANAGER_GLOBAL 0x19bbdd8ULL
#define MANAGER_CONTEXT 0x248ULL
#define CONTEXT_BATTLE 0x90ULL
#define BATTLE_HP_STATE 0xa8ULL
#define HP_STATE_REGISTRY 0x08ULL
#define REGISTRY_COLLECTION 0x40ULL
#define MAX_OBJECTS 2048
#define ENTITY_SIZE 0x124

static int read_exact(int fd, uint64_t address, void *output, size_t size);

static int read_battle_ui(int fd, uint64_t holder, int32_t *elixir, float *clock,
                          int32_t hand[4], int32_t *next_index, int32_t deck[8]) {
  uint64_t ui = 0, model = 0, hand_array = 0, queue = 0;
  if (!holder || !read_exact(fd, holder + 0xa8, &ui, 8) || !ui ||
      !read_exact(fd, holder + 0x290, &model, 8) || !model ||
      !read_exact(fd, ui + 0x1e0, elixir, 4) || !read_exact(fd, ui + 0x220, clock, 4) ||
      !read_exact(fd, model + 0x220, &hand_array, 8) || !hand_array ||
      !read_exact(fd, hand_array, hand, 16)) return 0;
  if (*elixir < 0 || *elixir > 10 || *clock < 0.0f || *clock > 600.0f) return 0;
  for (int i = 0; i < 4; ++i) if (hand[i] < 0 || hand[i] > 7) return 0;
  queue = 0;
  if (read_exact(fd, model + 0x230, &queue, 8) && queue)
    read_exact(fd, queue, next_index, 4);
  else *next_index = -1;
  if (!read_exact(fd, model + 0x288, deck, 32))
    for (int i = 0; i < 8; ++i) deck[i] = -1;
  return 1;
}

static int find_battle_ui(int fd, uint64_t roots[], int root_count,
                          int32_t *elixir, float *clock, int32_t hand[4],
                          int32_t *next_index, int32_t deck[8]) {
  for (int r = 0; r < root_count; ++r) {
    uint64_t root = roots[r];
    if (!root) continue;
    if (read_battle_ui(fd, root, elixir, clock, hand, next_index, deck)) return 1;
    // The tick holder is commonly a field of one of these manager objects.
    for (uint64_t offset = 0; offset <= 0x500; offset += 8) {
      uint64_t candidate = 0;
      if (!read_exact(fd, root + offset, &candidate, 8) || !candidate) continue;
      if (read_battle_ui(fd, candidate, elixir, clock, hand, next_index, deck)) return 1;
    }
  }
  return 0;
}

static int read_exact(int fd, uint64_t address, void *output, size_t size) {
  uint8_t *cursor = (uint8_t *)output;
  size_t done = 0;
  while (done < size) {
    ssize_t result = pread(fd, cursor + done, size - done, (off_t)(address + done));
    if (result <= 0) return 0;
    done += (size_t)result;
  }
  return 1;
}

static uint64_t find_libg_base(int pid) {
  char maps_path[64];
  char line[1024];
  uint64_t best = UINT64_MAX;
  snprintf(maps_path, sizeof(maps_path), "/proc/%d/maps", pid);
  FILE *maps = fopen(maps_path, "r");
  if (!maps) return 0;
  while (fgets(line, sizeof(line), maps)) {
    unsigned long long start = 0;
    unsigned long long offset = 0;
    char permissions[8] = {0};
    if (!strstr(line, "/libg.so")) continue;
    if (sscanf(line, "%llx-%*llx %7s %llx", &start, permissions, &offset) != 3) continue;
    if ((uint64_t)start >= (uint64_t)offset && (uint64_t)start - (uint64_t)offset < best)
      best = (uint64_t)start - (uint64_t)offset;
  }
  fclose(maps);
  return best == UINT64_MAX ? 0 : best;
}

static int32_t load_i32(const uint8_t *raw, size_t offset) {
  int32_t value;
  memcpy(&value, raw + offset, sizeof(value));
  return value;
}

static uint64_t load_u64(const uint8_t *raw, size_t offset) {
  uint64_t value;
  memcpy(&value, raw + offset, sizeof(value));
  return value;
}

static uint64_t monotonic_us(void) {
  struct timespec value;
  clock_gettime(CLOCK_MONOTONIC, &value);
  return (uint64_t)value.tv_sec * 1000000ULL + (uint64_t)value.tv_nsec / 1000ULL;
}

static int sane_entity(const uint8_t *raw) {
  int32_t category = load_i32(raw, 0x08);
  int32_t kind = load_i32(raw, 0x30);
  int32_t side = load_i32(raw, 0x78);
  int32_t x = load_i32(raw, 0x7c);
  int32_t y = load_i32(raw, 0x80);
  int32_t x2 = load_i32(raw, 0x84);
  int32_t y2 = load_i32(raw, 0x88);
  int32_t card_id = load_i32(raw, 0xac);
  int32_t level = load_i32(raw, 0x120);
  if (category < 5000000 || category >= 6000000) return 0;
  if (kind < 10 || kind > 20 || (side != 0 && side != 1)) return 0;
  if (x < 0 || x > 18000 || y < 0 || y > 32000) return 0;
  if (abs(x - x2) > 500 || abs(y - y2) > 500) return 0;
  if (level < 0 || level > 16) return 0;
  if (card_id != -1 && (card_id < 20000000 || card_id >= 1000000000)) return 0;
  return 1;
}

int main(int argc, char **argv) {
  if (argc < 2 || argc > 3) {
    fprintf(stderr, "usage: cr-arm-entity-stream PID [INTERVAL_MS]\n");
    return 2;
  }
  int pid = atoi(argv[1]);
  int interval_ms = argc == 3 ? atoi(argv[2]) : 100;
  if (pid <= 0 || interval_ms < 10 || interval_ms > 5000) return 2;

  uint64_t libg = find_libg_base(pid);
  if (!libg) {
    fprintf(stderr, "libg.so mapping not found\n");
    return 3;
  }
  char memory_path[64];
  snprintf(memory_path, sizeof(memory_path), "/proc/%d/mem", pid);
  int fd = open(memory_path, O_RDONLY | O_CLOEXEC);
  if (fd < 0) {
    fprintf(stderr, "open %s failed: %s\n", memory_path, strerror(errno));
    return 4;
  }
  setvbuf(stdout, NULL, _IONBF, 0);

  uint64_t sequence = 0;
  uint64_t addresses[MAX_OBJECTS];
  uint8_t raw[ENTITY_SIZE];
  for (;;) {
    uint64_t started_us = monotonic_us();
    uint64_t manager = 0, context = 0, battle = 0, hp_state = 0;
    uint64_t registry = 0, collection = 0, data = 0;
    int32_t count = 0;
    int active =
        read_exact(fd, libg + MANAGER_GLOBAL, &manager, 8) && manager &&
        read_exact(fd, manager + MANAGER_CONTEXT, &context, 8) && context &&
        read_exact(fd, context + CONTEXT_BATTLE, &battle, 8) && battle &&
        read_exact(fd, battle + BATTLE_HP_STATE, &hp_state, 8) && hp_state &&
        read_exact(fd, hp_state + HP_STATE_REGISTRY, &registry, 8) && registry &&
        read_exact(fd, registry + REGISTRY_COLLECTION, &collection, 8) && collection &&
        read_exact(fd, collection + 0x08, &data, 8) && data &&
        read_exact(fd, collection + 0x14, &count, 4) && count >= 0 && count <= MAX_OBJECTS &&
        read_exact(fd, data, addresses, (size_t)count * sizeof(addresses[0]));

    int32_t elixir = -1, hand[4] = {-1, -1, -1, -1}, next_index = -1, deck[8];
    float battle_clock = -1.0f;
    for (int i = 0; i < 8; ++i) deck[i] = -1;
    uint64_t roots[] = {manager, context, battle, hp_state, registry, collection};
    int have_battle_ui = active && find_battle_ui(fd, roots, (int)(sizeof(roots) / sizeof(roots[0])),
                                                  &elixir, &battle_clock, hand, &next_index, deck);

    printf("{\"event\":\"entity_stream\",\"sequence\":%" PRIu64
           ",\"battle_active\":%s,\"own_elixir\":%d,\"battle_clock\":%.3f,\"hand\":[",
           sequence++, active ? "true" : "false", have_battle_ui ? elixir : -1,
           have_battle_ui ? battle_clock : -1.0f);
    if (have_battle_ui) for (int i = 0; i < 4; ++i) {
      if (i) putchar(',');
      int id = (hand[i] >= 0 && hand[i] < 8) ? deck[hand[i]] : -1;
      printf("{\"slot\":%d,\"deck_index\":%d,\"data_id\":%d}", i, hand[i], id);
    }
    printf("],\"next_card\":{\"deck_index\":%d,\"data_id\":%d},\"entities\":[", next_index,
           next_index >= 0 && next_index < 8 ? deck[next_index] : -1);
    int emitted = 0;
    if (active) {
      for (int32_t index = 0; index < count; ++index) {
        uint64_t address = addresses[index];
        uint64_t owner = 0, component = 0;
        int32_t hp_values[2] = {-1, -1};
        if (!address || !read_exact(fd, address, raw, sizeof(raw)) || !sane_entity(raw))
          continue;
        owner = load_u64(raw, 0x18);
        if (!owner || !read_exact(fd, owner + 0x10, &component, 8) || !component ||
            !read_exact(fd, component + 0x10, hp_values, sizeof(hp_values)))
          continue;
        if (hp_values[0] < 0 || hp_values[1] < hp_values[0] || hp_values[1] > 50000)
          continue;
        if (emitted++) putchar(',');
        printf("{\"address\":\"0x%" PRIx64
               "\",\"kind\":%d,\"side\":%d,\"x\":%d,\"y\":%d,"
               "\"x2\":%d,\"y2\":%d,\"card_id\":%d,\"level\":%d,"
               "\"hp\":%d,\"max_hp\":%d}",
               address, load_i32(raw, 0x30), load_i32(raw, 0x78),
               load_i32(raw, 0x7c), load_i32(raw, 0x80), load_i32(raw, 0x84),
               load_i32(raw, 0x88), load_i32(raw, 0xac),
               load_i32(raw, 0x120) + 1, hp_values[0], hp_values[1]);
      }
    }
    printf("],\"read_us\":%" PRIu64 "}\n", monotonic_us() - started_us);

    uint64_t elapsed_us = monotonic_us() - started_us;
    uint64_t target_us = (uint64_t)interval_ms * 1000ULL;
    if (elapsed_us < target_us) usleep((useconds_t)(target_us - elapsed_us));
  }
}
