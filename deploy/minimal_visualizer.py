import pygame
import json
from threading import Thread

from run_raw_capture import mainloop

pygame.init()
TILE = 22
AX, AY = 50, 50
AW, AH = 18*TILE, 32*TILE
W, H = AW+120, AH+100
BLUE, RED, GREEN, CYAN, DKGRAY, BLACK, WHITE = (100,100,255),(255,100,100),(100,255,100),(100,255,255),(64,64,64),(0,0,0),(255,255,255)

def w2s(x, y):
    return int(AX + x * TILE), int(AY + y * TILE)

with open('cards.json') as f:
    card_data = json.loads(f.read())
cards = {each['id']: each['name'] for each in card_data['items']}

class Visualizer:
    def __init__(self):
        """If given a battle object, then render that battle."""
        self.screen = pygame.display.set_mode((W, H))
        self.clock = pygame.time.Clock()
        self.font = pygame.font.Font(None, 18)
        self.entities = {}
        self.snapshot = {}
        self.local_player_index = None
        self.running = True

    def draw_arena(self):
        pygame.draw.rect(self.screen, GREEN, (AX,AY,AW,AH))
        ry = AY+15*TILE
        pygame.draw.rect(self.screen, CYAN, (AX, ry, AW, 2*TILE))
        for bx in [2, 13]:
            pygame.draw.rect(self.screen, DKGRAY, (AX+bx*TILE, ry, 3*TILE, 2*TILE))
        pygame.draw.rect(self.screen, DKGRAY, (AX, AY, 6*TILE, TILE))
        pygame.draw.rect(self.screen, DKGRAY, (AX+12*TILE, AY, 6 * TILE, TILE))
        pygame.draw.rect(self.screen, DKGRAY, (AX, AY+31*TILE, 6 * TILE, TILE))
        pygame.draw.rect(self.screen, DKGRAY, (AX + 12 * TILE, AY+31*TILE, 6 * TILE, TILE))
        for x in range(19): pygame.draw.line(self.screen, (0,150,0), (AX+x*TILE,AY), (AX+x*TILE,AY+AH), 1)
        for y in range(33): pygame.draw.line(self.screen, (0,150,0), (AX,AY+y*TILE), (AX+AW,AY+y*TILE), 1)

    def draw_entities(self):
        for entity_ptr, entity in list(self.entities.items()):
            if entity['card_id_ac'] == -1 and entity['kind_30'] in (12, 13):
                name = "CrownTower"
            else:
                if entity['card_id_ac'] in cards:
                    name = cards[entity['card_id_ac']]
                elif entity['card_id_ac'] == -1: continue
                elif str(entity['card_id_ac']).startswith('13'):
                    real_id = entity['card_id_ac'] + 13000000
                    name = 'Evo ' + cards[real_id]
                elif str(entity['card_id_ac']).startswith('203'):
                    real_id = entity['card_id_ac'] - 177000000
                    name = "Hero" + str(cards.get(real_id))
                else:
                    print('Entity unknown:', entity['card_id_ac'])
                    name = entity['card_id_ac']
            r = 0.5 * TILE
            if self.local_player_index == 1:
                x, y = entity['pos_x_7c']/1000, entity['pos_y_80']/1000
                color = BLUE if entity['side_78'] == 1 else RED
            else:
                x, y = 18-entity['pos_x_7c']/1000, 32-entity['pos_y_80']/1000
                color = BLUE if entity['side_78'] == 0 else RED
            sx, sy = w2s(x, y)
            pygame.draw.circle(self.screen, color, (sx, sy), max(r, 4))
            pygame.draw.circle(self.screen, BLACK, (sx, sy), max(r, 4), 1)
            lbl = self.font.render(str(name), True, BLACK)
            self.screen.blit(lbl, lbl.get_rect(center=(sx, sy + r + 10)))

            bw = max(r * 2, 16)
            max_hp = entity.get('max_hp_14')
            hp_width = (entity['hp_10'] / max_hp) * bw if max_hp > 0 else bw
            pygame.draw.rect(self.screen, BLACK, (sx - bw // 2 - 1, sy - r - 12, bw + 2, 5))
            pygame.draw.rect(self.screen, GREEN, (sx - bw // 2, sy - r - 11, hp_width, 3))
            hp_txt = self.font.render(str(int(entity['hp_10'])), True, WHITE)
            self.screen.blit(hp_txt, hp_txt.get_rect(center=(sx, sy)))

    def draw_ui(self):
        hand = []
        for each in self.snapshot['hand']:
            data_id = each['data_id_40']
            if data_id in cards:
                hand.append(cards[data_id])
            else:
                print('Unknown card in hand:' , data_id)
                hand.append(str(data_id))
        text = f"t={self.snapshot['battle_clock_220']:.1f}s elixir={self.snapshot['own_elixir_1e0']} hand={hand}"
        txt = self.font.render(text, True, BLACK)
        self.screen.blit(txt, (AX, AY+AH+10))

    def process_events(self):
        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                self.running = False

    def render_frame(self):
        self.screen.fill(WHITE)
        self.clock.tick(60)
        self.draw_arena()
        self.draw_entities()
        self.draw_ui()
        pygame.display.flip()

    def run(self):
        while self.running:
            self.process_events()
            if self.snapshot and self.local_player_index is not None:
                if self.snapshot['battle_clock_220'] is not None:
                    self.render_frame()
        pygame.quit()

window = Visualizer()
t = Thread(target=mainloop, args=(window, ))
t.start()
window.run()
