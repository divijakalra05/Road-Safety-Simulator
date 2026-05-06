"""
Road Safety Simulator v4.0
────────────────────────────────────────────────────────
v4 CHANGES:
  ✦ INFINITE procedurally generated road (no finish line)
  ✦ Game ends ONLY on collision (game over) — survive as long as possible
  ✦ HIGH SCORE system — beat your best distance + score
  ✦ Fixed gear physics: higher gear = MORE top speed, correct torque bands
  ✦ Engine sound only — all other sounds removed
  ✦ Realistic physics: weight transfer, tyre grip, momentum, gear inertia
  ✦ Car selection screen with 8 models
  ✦ Dynamic weather (clear/rain/night), AI coach, skid marks, fuel
────────────────────────────────────────────────────────
pip install pygame numpy requests
"""

import pygame, sys, random, math, threading, os
import numpy as np

try:
    import requests; REQUESTS_OK = True
except ImportError:
    REQUESTS_OK = False

# ── INIT ──────────────────────────────────────────────
pygame.init()
pygame.mixer.init(frequency=44100, size=-16, channels=2, buffer=512)

W, H  = 1000, 660
FPS   = 60
screen = pygame.display.set_mode((W, H))
pygame.display.set_caption("Road Safety Simulator v4.0 🚗")
clock  = pygame.time.Clock()

# ── COLOURS ───────────────────────────────────────────
C_SKY_TOP  =(40,100,200);  C_SKY_BOT  =(135,206,235)
C_NIGHT_TOP=(5,5,25);      C_NIGHT_BOT=(20,20,60)
C_RAIN_TOP =(60,70,90);    C_RAIN_BOT =(100,120,145)
C_ROAD     =(48,48,52);    C_ROAD_WET =(38,42,50)
C_LANE     =(220,220,60);  C_LANE_SOL =(255,255,255)
C_SIDEWALK =(170,150,120); C_GRASS    =(60,140,50)
C_GRASS_D  =(40,100,35);   C_ASPH_L   =(62,62,68)
C_WHITE=(255,255,255); C_BLACK=(0,0,0)
C_RED=(220,40,40);     C_GREEN=(40,200,80)
C_AMBER=(255,165,0);   C_DARK_GREY=(28,28,33)
C_HUD_BG=(10,10,18);   C_PENALTY=(255,60,60)
C_SCORE_C=(60,220,120);C_GUARDRAIL=(180,180,190)
C_RAIN_C=(160,190,220);C_HEADLIGHT=(255,255,200)
C_BRAKE=(255,60,30)

ROAD_TOP=238; ROAD_BOT=442
FOOT_TOP_Y=200   # top footpath starts here
FOOT_BOT_Y=480   # bottom footpath ends here
FOOT_H=35        # footpath height px
ROAD_MID=(ROAD_TOP+ROAD_BOT)//2
LANE_H=(ROAD_BOT-ROAD_TOP)//2
SPEED_LIMIT=4.5   # slightly higher limit for fun

# ── FONTS ─────────────────────────────────────────────
def _f(n,s,b=False):
    try:    return pygame.font.SysFont(n,s,bold=b)
    except: return pygame.font.SysFont(None,s,bold=b)

F_BIG  =_f("Segoe UI",34,True); F_MED  =_f("Segoe UI",22)
F_SM   =_f("Segoe UI",15);      F_TITLE=_f("Impact",58,True)
F_MONO =_f("Courier New",14);   F_HUGE =_f("Impact",72,True)

# ── ENGINE SOUND ONLY ─────────────────────────────────
def _tone(freq,vol=0.18,ms=60):
    sr=44100; n=int(sr*ms/1000)
    # blend sine + slight harmonic for warmth
    mono=[int(vol*32767*(0.7*math.sin(2*math.pi*freq*i/sr)+
                         0.3*math.sin(2*math.pi*freq*2*i/sr)))
          for i in range(n)]
    arr=np.array([v for v in mono for _ in range(2)],dtype=np.int16).reshape(n,2)
    return pygame.sndarray.make_sound(arr)

# 20 engine tones spanning idle (55Hz) to redline (310Hz)
ENGINE_TONES={r:_tone(55+r*13,vol=0.08+r*0.006,ms=60) for r in range(20)}
_eng_tick=0

def play_engine(rpm_idx):
    global _eng_tick
    now=pygame.time.get_ticks()
    if now-_eng_tick>55:
        _eng_tick=now
        idx=max(0,min(19,rpm_idx))
        try: ENGINE_TONES[idx].play()
        except: pass

# ── HELPERS ───────────────────────────────────────────
def rr(surf,col,rect,r=10,a=255):
    s=pygame.Surface((rect[2],rect[3]),pygame.SRCALPHA)
    pygame.draw.rect(s,(*col[:3],a),(0,0,rect[2],rect[3]),border_radius=r)
    surf.blit(s,(rect[0],rect[1]))

def txt(surf,text,font,col,cx,cy,shadow=False,alpha=255):
    if shadow:
        s=font.render(text,True,(0,0,0)); s.set_alpha(alpha)
        surf.blit(s,s.get_rect(center=(cx+2,cy+2)))
    r=font.render(text,True,col); r.set_alpha(alpha)
    surf.blit(r,r.get_rect(center=(cx,cy)))

def lerp_c(a,b,t): return tuple(int(a[i]+(b[i]-a[i])*t) for i in range(3))

# ── HIGH SCORE ────────────────────────────────────────
_HS_FILE="road_sim_highscore.txt"
def load_hs():
    try:
        with open(_HS_FILE) as f: return int(f.read().strip())
    except: return 0
def save_hs(s):
    try:
        with open(_HS_FILE,"w") as f: f.write(str(s))
    except: pass

HIGH_SCORE=load_hs()

# ── AI COACH ──────────────────────────────────────────
AI_KEY=os.environ.get("ANTHROPIC_API_KEY","")
FALLBACK=[
    "Keep 3 seconds gap from the car ahead",
    "Check mirrors every 5-8 seconds",
    "Slow down before a bend, not in it",
    "Always stop at the stop line on red",
    "Pedestrians have right of way at crossings",
    "Speeding triples your stopping distance",
    "Shift up early to save fuel and stay smooth",
    "Rain doubles braking distance — slow down",
    "Smooth inputs preserve tyre grip",
    "Higher gears reduce engine braking — plan ahead",
]

class AICoach:
    def __init__(self):
        self.tip="Drive safely and survive as long as possible!"
        self.loading=False; self._lock=threading.Lock(); self._cd=0
    def request_tip(self,ctx):
        if not REQUESTS_OK or not AI_KEY:
            self.tip=random.choice(FALLBACK); return
        if self.loading or self._cd>0: return
        self.loading=True
        threading.Thread(target=self._fetch,args=(ctx,),daemon=True).start()
    def _fetch(self,ctx):
        try:
            r=requests.post("https://api.anthropic.com/v1/messages",
              headers={"x-api-key":AI_KEY,"anthropic-version":"2023-06-01","content-type":"application/json"},
              json={"model":"claude-sonnet-4-20250514","max_tokens":80,
                    "messages":[{"role":"user","content":
                      f"Road-safety coach. Situation: {ctx}. "
                      f"One short punchy tip max 12 words, no emoji, no quotes."}]},timeout=8)
            with self._lock: self.tip=r.json()["content"][0]["text"].strip().rstrip(".")
        except:
            with self._lock: self.tip=random.choice(FALLBACK)
        finally: self.loading=False
    def tick(self):
        if self._cd>0: self._cd-=1
    def refresh(self,ctx): self._cd=0; self.request_tip(ctx)

ai=AICoach()

# ── WEATHER (no fog) ──────────────────────────────────
class Weather:
    MODES=["clear","rain","night"]
    def __init__(self):
        self.mode="clear"; self.drops=[]
        self._t=random.randint(1800,3000); self._gen()
    def _gen(self):
        self.drops=[[random.randint(0,W),random.randint(0,H),
                     random.uniform(8,18),random.uniform(1,3)] for _ in range(280)]
    def update(self):
        self._t-=1
        if self._t<=0:
            self.mode=random.choice([m for m in self.MODES if m!=self.mode])
            self._t=random.randint(1800,3000)
            if self.mode=="rain": self._gen()
        if self.mode=="rain":
            for d in self.drops:
                d[0]-=d[3]; d[1]+=d[2]
                if d[1]>H: d[0]=random.randint(0,W); d[1]=-10
    def sky(self):
        if self.mode=="night": return C_NIGHT_TOP,C_NIGHT_BOT
        if self.mode=="rain":  return C_RAIN_TOP,C_RAIN_BOT
        return C_SKY_TOP,C_SKY_BOT
    def road_col(self): return C_ROAD_WET if self.mode=="rain" else C_ROAD
    def draw_fx(self,surf):
        if self.mode=="rain":
            for d in self.drops:
                x,y,sp,w2=d
                pygame.draw.line(surf,(*C_RAIN_C,120),(int(x),int(y)),(int(x-w2*.5),int(y+sp*1.5)),1)
        if self.mode=="night":
            dk=pygame.Surface((W,H),pygame.SRCALPHA); dk.fill((0,0,10,120)); surf.blit(dk,(0,0))

weather=Weather()

# ── SKID MARKS ────────────────────────────────────────
skids=[]  # [world_x, world_y, alpha, width]
def add_skid(wx,wy,w=12):
    skids.append([wx,wy,220,w])
def update_skids():
    for s in skids: s[2]=max(0,s[2]-0.5)
    skids[:]=[s for s in skids if s[2]>0]
def draw_skids(surf,cam_x):
    for s in skids:
        sx=int(s[0]-cam_x)
        if -20<sx<W+20:
            c=pygame.Surface((s[3],5),pygame.SRCALPHA)
            c.fill((15,15,15,int(s[2]))); surf.blit(c,(sx,int(s[1])-2))

# ── TRAFFIC LIGHT ─────────────────────────────────────
class TrafficLight:
    PH=["green","amber","red"]; TM={"green":300,"amber":55,"red":240}
    def __init__(self,x):
        self.x=x; self.phase=random.choice(["green","red"])
        self.timer=self.TM[self.phase]
    def update(self):
        self.timer-=1
        if self.timer<=0:
            self.phase=self.PH[(self.PH.index(self.phase)+1)%3]
            self.timer=self.TM[self.phase]
    def is_red(self): return self.phase=="red"
    def is_amber(self): return self.phase=="amber"
    def stop_x(self): return self.x-35
    def draw(self,surf,cam_x):
        sx=int(self.x-cam_x)
        if sx<-80 or sx>W+80: return
        pygame.draw.rect(surf,(55,55,60),(sx-4,ROAD_TOP-95,7,95))
        pygame.draw.rect(surf,(55,55,60),(sx-4,ROAD_TOP-95,28,6))
        bx,by,bw,bh=sx+8,ROAD_TOP-95,28,72
        pygame.draw.rect(surf,C_DARK_GREY,(bx,by,bw,bh),border_radius=6)
        CM={"red":C_RED,"amber":C_AMBER,"green":C_GREEN}
        for name,off in zip(["red","amber","green"],[0,24,48]):
            cy_=by+11+off; cx_=bx+bw//2
            col=CM[name] if self.phase==name else (25,25,25)
            if self.phase==name:
                g=pygame.Surface((40,40),pygame.SRCALPHA)
                pygame.draw.circle(g,(*CM[name],70),(20,20),20); surf.blit(g,(cx_-20,cy_-20))
            pygame.draw.circle(surf,col,(cx_,cy_),10)
        stop=int(self.stop_x()-cam_x)
        pygame.draw.rect(surf,C_WHITE,(stop-2,ROAD_TOP,4,ROAD_BOT-ROAD_TOP))
        secs=max(0,self.timer//FPS)
        txt(surf,str(secs),F_SM,CM[self.phase],bx+bw//2,by+bh+10)

# ── PEDESTRIAN & ZEBRA ────────────────────────────────
class Pedestrian:
    COATS=[(200,80,80),(80,80,200),(80,180,80),(180,120,60),(200,120,180)]
    def __init__(self,x,sy,d):
        self.x=x; self.y=float(sy); self.dir=d
        self.speed=random.uniform(0.9,1.6); self.done=False
        self.coat=random.choice(self.COATS)
        self.skin=random.choice([(220,180,140),(160,110,70)]); self.ph=0
    def update(self):
        self.y+=self.speed*self.dir; self.ph+=0.2
        if self.dir==1 and self.y>FOOT_BOT_Y+10: self.done=True
        if self.dir==-1 and self.y<FOOT_TOP_Y-10: self.done=True
    def draw(self,surf,cam_x):
        sx=int(self.x-cam_x); sy=int(self.y); sw=math.sin(self.ph)*4
        sh=pygame.Surface((20,8),pygame.SRCALPHA)
        pygame.draw.ellipse(sh,(0,0,0,55),(0,0,20,8)); surf.blit(sh,(sx-10,sy+14))
        pygame.draw.line(surf,(40,40,80),(sx,sy+8),(sx-4+int(sw),sy+18),3)
        pygame.draw.line(surf,(40,40,80),(sx,sy+8),(sx+4-int(sw),sy+18),3)
        pygame.draw.rect(surf,self.coat,(sx-5,sy-4,10,14),border_radius=3)
        pygame.draw.line(surf,self.coat,(sx-5,sy),(sx-9+int(sw),sy+7),2)
        pygame.draw.line(surf,self.coat,(sx+5,sy),(sx+9-int(sw),sy+7),2)
        pygame.draw.circle(surf,self.skin,(sx,sy-8),6)
    def get_rect(self,cam_x):
        return pygame.Rect(int(self.x-cam_x)-6,int(self.y)-14,12,32)

class ZebraCrossing:
    def __init__(self,x,light):
        self.x=x; self.light=light; self.peds=[]
        self.st=0; self.se=random.randint(200,380)
    def update(self):
        self.st+=1
        if self.st>=self.se and self.light.is_red():
            self.st=0; self.se=random.randint(200,380)
            side=random.choice([-1,1])
            sy=FOOT_TOP_Y+8 if side==1 else FOOT_BOT_Y-8
            for _ in range(random.randint(1,2)):
                self.peds.append(Pedestrian(self.x+15+random.randint(-8,8),sy,side))
        for p in self.peds[:]:
            p.update()
            if p.done: self.peds.remove(p)
    def draw(self,surf,cam_x):
        sx=int(self.x-cam_x)
        if sx<-80 or sx>W+80: return
        for i in range((ROAD_BOT-ROAD_TOP)//24+1):
            pygame.draw.rect(surf,C_WHITE,(sx-22,ROAD_TOP+i*24,44,11))
        for p in self.peds: p.draw(surf,cam_x)

# ── CAR MODELS ────────────────────────────────────────
CAR_CATALOGUE=[
    ("sports",   "Sports Car",   (220,50,40),   66,24,"Fast & low. High top speed."),
    ("sedan",    "Sedan",        (40,80,200),   66,28,"Balanced everyday car."),
    ("suv",      "SUV",          (50,160,60),   70,34,"Tall & sturdy. Good control."),
    ("muscle",   "Muscle Car",   (200,140,20),  72,28,"Wide body, strong torque."),
    ("hatchback","Hatchback",    (200,80,180),  60,26,"Compact & nimble."),
    ("pickup",   "Pickup Truck", (160,90,40),   80,30,"Heavy but powerful."),
    ("supercar", "Supercar",     (20,200,220),  68,22,"Ultra-fast. Hard to control."),
    ("van",      "Van",          (230,230,230), 88,36,"Wide load. Slow but stable."),
]
NPC_MODELS =["sedan","suv","sports","hatchback","pickup","muscle","van"]
NPC_COLORS =[(180,30,30),(30,60,200),(30,160,50),(200,140,30),
             (140,30,180),(30,160,190),(210,210,210),(50,50,60)]

def draw_car(surf,model,color,sx,sy,w,h,braking=False,headlights=False,flipped=False):
    fl=-1 if flipped else 1
    hw,hh=w//2,h//2
    darker =tuple(max(0,c-55) for c in color)
    lighter=tuple(min(255,c+65) for c in color)
    roof_c =tuple(max(0,c-75) for c in color)
    sh=pygame.Surface((w+14,10),pygame.SRCALPHA)
    pygame.draw.ellipse(sh,(0,0,0,48),(0,0,w+14,10)); surf.blit(sh,(sx-hw-7,sy+hh-3))

    if model=="sedan":
        pygame.draw.rect(surf,color,(sx-hw,sy-hh,w,h),border_radius=9)
        pts=[(sx+fl*hw,sy-hh+4),(sx+fl*hw,sy+hh-4),(sx+fl*(hw-14),sy-hh+4),(sx+fl*(hw-14),sy+hh-4)]
        pygame.draw.polygon(surf,darker,[pts[0],pts[2],pts[3],pts[1]])
        rw=int(w*.50); rh2=16; rx=sx-rw//2+fl*4
        pygame.draw.rect(surf,roof_c,(rx,sy-hh-rh2,rw,rh2),border_radius=6)
        pygame.draw.rect(surf,(140,205,240),(rx+2,sy-hh-rh2+2,rw-4,rh2-3),border_radius=4)
        pygame.draw.rect(surf,(140,205,240),(sx-rw//2-fl*10,sy-hh-rh2+2,rw//2-4,rh2-3),border_radius=3)
        pygame.draw.line(surf,darker,(sx-fl*8,sy-hh+3),(sx-fl*8,sy+hh-3),1)
        for dy in [-4,4]: pygame.draw.rect(surf,lighter,(sx-fl*14,sy+dy,8,3),border_radius=1)
    elif model=="suv":
        pygame.draw.rect(surf,color,(sx-hw,sy-hh,w,h),border_radius=7)
        rw=int(w*.72); rx=sx-rw//2+fl*2
        pygame.draw.rect(surf,roof_c,(rx,sy-hh-14,rw,14),border_radius=4)
        pygame.draw.rect(surf,(140,205,240),(rx+2,sy-hh-12,rw-4,10),border_radius=3)
        pygame.draw.line(surf,darker,(sx-hw+4,sy-2),(sx+hw-4,sy-2),2)
        for i in range(3): pygame.draw.rect(surf,(160,160,165),(rx+4+i*12,sy-hh-16,10,3))
    elif model=="sports":
        pts2=[(sx-hw,sy+hh-2),(sx-hw+int(w*.12),sy-hh+4),(sx-hw+int(w*.35),sy-hh),
             (sx+hw-int(w*.25),sy-hh),(sx+hw-int(w*.08),sy-hh+6),(sx+hw,sy+hh-2)]
        if flipped: pts2=[(sx+(sx-p[0]),p[1]) for p in pts2]
        pygame.draw.polygon(surf,color,pts2); pygame.draw.polygon(surf,darker,pts2,1)
        cw=int(w*.28); cx0=sx-int(w*.05)*fl
        pygame.draw.rect(surf,roof_c,(cx0-cw//2,sy-hh-10,cw,11),border_radius=5)
        pygame.draw.rect(surf,(140,215,250),(cx0-cw//2+1,sy-hh-8,cw-2,8),border_radius=4)
        for vy in [-4,4]: pygame.draw.line(surf,darker,(sx-fl*20,sy+vy),(sx-fl*30,sy+vy),1)
        pygame.draw.rect(surf,(30,30,35),(sx-fl*(hw-5)-4,sy-hh-5,8,5))
    elif model=="muscle":
        pygame.draw.rect(surf,color,(sx-hw,sy-hh,w,h),border_radius=6)
        pygame.draw.rect(surf,lighter,(sx-hw+fl*4,sy-hh,int(w*.38),hh),border_radius=4)
        rw=int(w*.55); rx=sx-rw//2+fl*5
        pygame.draw.rect(surf,roof_c,(rx,sy-hh-13,rw,13),border_radius=5)
        pygame.draw.rect(surf,(140,210,245),(rx+2,sy-hh-11,rw-4,9),border_radius=4)
        pygame.draw.rect(surf,C_WHITE,(sx-4,sy-hh,8,h))
        pygame.draw.line(surf,darker,(sx-hw+4,sy),(sx+hw-4,sy),2)
    elif model=="hatchback":
        pygame.draw.rect(surf,color,(sx-hw,sy-hh,w,h),border_radius=10)
        rw=int(w*.62); rx=sx-rw//2+fl*2
        pygame.draw.rect(surf,roof_c,(rx,sy-hh-12,rw,12),border_radius=5)
        pygame.draw.rect(surf,(140,210,250),(rx+2,sy-hh-10,rw-4,8),border_radius=4)
        pygame.draw.line(surf,darker,(sx-fl*(hw-2),sy-hh+3),(sx-fl*(hw-2),sy+hh-3),2)
    elif model=="pickup":
        cw=int(w*.45); cx_=sx+fl*(hw-cw)
        pygame.draw.rect(surf,color,(min(cx_,sx-hw),sy-hh,cw,h),border_radius=7)
        pygame.draw.rect(surf,(140,210,245),(min(cx_,sx-hw)+2,sy-hh+2,cw-4,hh-2),border_radius=4)
        bw=w-cw-4; bx=min(sx-hw+cw+4,sx+fl*hw-bw)
        pygame.draw.rect(surf,darker,(bx,sy-hh+4,bw,h-8),border_radius=3)
        pygame.draw.rect(surf,lighter,(bx,sy-hh+4,bw,4))
        pygame.draw.rect(surf,lighter,(bx,sy+hh-8,bw,4))
    elif model=="supercar":
        pts3=[(sx-hw,sy+hh-1),(sx-hw+int(w*.08),sy-hh+6),(sx-hw+int(w*.28),sy-hh),
             (sx+hw-int(w*.20),sy-hh),(sx+hw-int(w*.04),sy-hh+8),(sx+hw,sy+hh-1)]
        if flipped: pts3=[(sx+(sx-p[0]),p[1]) for p in pts3]
        pygame.draw.polygon(surf,color,pts3); pygame.draw.polygon(surf,darker,pts3,2)
        cw=int(w*.22); cx0=sx-int(w*.08)*fl
        pygame.draw.rect(surf,roof_c,(cx0-cw//2,sy-hh-8,cw,9),border_radius=4)
        pygame.draw.rect(surf,(160,225,255),(cx0-cw//2+1,sy-hh-6,cw-2,6),border_radius=3)
        for vy in [-3,3]: pygame.draw.line(surf,darker,(sx-fl*18,sy+vy),(sx-fl*28,sy+vy),2)
        pygame.draw.rect(surf,(20,20,25),(sx-fl*(hw-3)-6,sy-hh-7,12,5))
        pygame.draw.rect(surf,(20,20,25),(sx-fl*(hw-3)-6,sy-hh-12,12,3))
    elif model=="van":
        pygame.draw.rect(surf,color,(sx-hw,sy-hh,w,h),border_radius=4)
        ww,wh=20,int(h*.40)
        for i in range(1,w//26):
            wx2=sx-hw+8+i*26
            if abs(wx2-sx)<hw-6:
                pygame.draw.rect(surf,(160,220,250),(wx2,sy-hh+4,ww,wh),border_radius=2)
        pygame.draw.rect(surf,(20,20,25),(sx-hw,sy-hh+wh+5,w,4))
        pygame.draw.rect(surf,(255,220,50),(sx-hw+4,sy-hh+1,60,10),border_radius=2)
        pygame.draw.line(surf,darker,(sx,sy-hh+3),(sx,sy+hh-3),2)

    # wheels
    for wy2 in [sy-hh+6,sy+hh-6]:
        for wo in [hw-11,-(hw-11)]:
            wx2=sx+wo; r2=8 if model not in ("van","pickup","muscle") else 10
            pygame.draw.circle(surf,(22,22,22),(wx2,wy2),r2)
            pygame.draw.circle(surf,(180,185,195),(wx2,wy2),r2-3)
            for ang in range(0,360,60):
                a=math.radians(ang)
                pygame.draw.line(surf,(150,155,165),(wx2,wy2),(wx2+int((r2-3)*math.cos(a)),wy2+int((r2-3)*math.sin(a))),1)
            pygame.draw.circle(surf,(80,80,85),(wx2,wy2),2)
    # headlights
    if headlights:
        fx=sx+fl*(hw-3)
        for ly in [sy-hh+5,sy+hh-5]:
            g=pygame.Surface((30,30),pygame.SRCALPHA)
            pygame.draw.circle(g,(*C_HEADLIGHT,80),(15,15),15); surf.blit(g,(fx-15,ly-15))
            pygame.draw.circle(surf,C_HEADLIGHT,(fx,ly),5)
    # brake lights
    if braking:
        rx_=sx-fl*(hw-3)
        for ly in [sy-hh+5,sy+hh-5]:
            g=pygame.Surface((22,22),pygame.SRCALPHA)
            pygame.draw.circle(g,(*C_BRAKE,110),(11,11),11); surf.blit(g,(rx_-11,ly-11))
            pygame.draw.circle(surf,C_BRAKE,(rx_,ly),4)
    # plate
    px=sx-fl*(hw-2)
    pygame.draw.rect(surf,(250,250,200),(px-12,sy+2,24,9),border_radius=1)

# ── NPC CAR ───────────────────────────────────────────
class NPCCar:
    def __init__(self,x,lane,speed):
        self.x=float(x); self.lane=lane
        self.y=ROAD_TOP+lane*LANE_H+LANE_H//2
        self.speed=speed; self.target_speed=speed
        self.model=random.choice(NPC_MODELS); self.color=random.choice(NPC_COLORS)
        dm={"sedan":64,"suv":68,"sports":60,"hatchback":58,"pickup":80,"muscle":70,"van":90}
        hm={"sedan":28,"suv":32,"sports":24,"hatchback":26,"pickup":30,"muscle":28,"van":36}
        self.w=dm.get(self.model,64); self.h=hm.get(self.model,28)
        self.braking=False; self._cs=speed
    def update(self,lights,npcs):
        self.braking=False
        for lt in lights:
            if (lt.is_red() or lt.is_amber()) and self.lane==0:
                sx=lt.stop_x()
                if 0<sx-self.x<160:
                    d=sx-self.x; self.target_speed=self.speed*max(0,(d-10)/150)
                    self.braking=True; break
        else:
            self.target_speed=self.speed
        for o in npcs:
            if o is self or o.lane!=self.lane: continue
            gap=o.x-self.x
            if 0<gap<110:
                self.target_speed=max(0,o._cs-0.2) if gap<40 else min(self.speed,o._cs)
                self.braking=gap<60
        self._cs+=(self.target_speed-self._cs)*.06
        self._cs=max(0,self._cs); self.x+=self._cs
    def draw(self,surf,cam_x):
        sx=int(self.x-cam_x)
        if sx<-130 or sx>W+130: return
        draw_car(surf,self.model,self.color,sx,int(self.y),self.w,self.h,
                 braking=self.braking,headlights=weather.mode=="night",flipped=self.lane==1)
    def get_rect(self,cam_x):
        return pygame.Rect(int(self.x-cam_x)-self.w//2,int(self.y)-self.h//2,self.w,self.h)

# ── GEAR SYSTEM ───────────────────────────────────────
# Each gear: (unused_placeholder, speed_cap_px_per_frame)
# Gear UP (Q) = raises speed cap → car keeps accelerating to new higher cap
# Gear DN (E) = lowers speed cap → drag+engine braking slows car down
GEARS=[
    None,
    (1.0, 2.2),   # G1 ~40 km/h
    (1.0, 3.8),   # G2 ~68 km/h
    (1.0, 5.2),   # G3 ~94 km/h
    (1.0, 6.5),   # G4 ~117 km/h
    (1.0, 7.8),   # G5 ~140 km/h
    (1.0, 8.8),   # G6 ~158 km/h
]

# ── PLAYER CAR ────────────────────────────────────────
class PlayerCar:
    # Physics constants
    ENGINE_FORCE_MAX = 0.62   # peak force (px/frame²)
    TORQUE_FALLOFF   = 0.80
    TOP_SPEED        = 8.8    # absolute cap (px/frame)
    ROLL_RESIST      = 0.010
    AIR_DRAG         = 0.0042
    BRAKE_FORCE      = 0.48
    REVERSE_FORCE    = 0.16
    REVERSE_MAX      = 2.2
    STEER_ACCEL      = 0.36
    STEER_DAMP       = 0.76
    STEER_MAX        = 3.2
    # Weight transfer: braking shifts weight forward → rear grip loss
    WEIGHT_TRANSFER  = 0.18

    def __init__(self,model,color,w,h):
        self.x=120.0; self.y=float(ROAD_MID-LANE_H//2)
        self.vx=0.0; self.vy=0.0; self.vy_damp=0.0
        self.model=model; self.color=color; self.w=w; self.h=h
        self.on_road=True; self.throttle=0.0
        self.gear=1; self.fuel=100.0
        self.gear_warn=""; self.gear_warn_t=0
        self._gku=False; self._gkd=False   # gear key edge detect
        # inertia: engine takes time to respond
        self._engine_out=0.0

    def _torque(self,speed,gear):
        _,g_cap=GEARS[gear]
        # Torque is strong from 0 up to the gear cap, then falls off
        t=max(0.0,1.0-(speed/max(g_cap,0.1))**self.TORQUE_FALLOFF)
        return self.ENGINE_FORCE_MAX * t

    def update(self,keys):
        accel  =keys[pygame.K_RIGHT] or keys[pygame.K_d]
        brake  =keys[pygame.K_SPACE]
        rev    =keys[pygame.K_LEFT]  or keys[pygame.K_a]
        up_s   =keys[pygame.K_UP]   or keys[pygame.K_w]
        dn_s   =keys[pygame.K_DOWN] or keys[pygame.K_s]
        gku    =keys[pygame.K_q]
        gkd    =keys[pygame.K_e]

        # ── gear shift (edge-triggered, no sound) ──
        if gku and not self._gku and self.gear<6: self.gear+=1
        if gkd and not self._gkd and self.gear>1: self.gear-=1
        self._gku=gku; self._gkd=gkd

        speed=abs(self.vx); sign=1 if self.vx>=0 else -1
        _,g_max=GEARS[self.gear]

        # ── throttle smoothing ──
        if accel and not brake: self.throttle=min(1.0,self.throttle+0.05)
        else:                   self.throttle=max(0.0,self.throttle-0.08)

        # ── gear warning ──
        self.gear_warn=""
        if self.vx > 0.5:
            if speed >= g_max*0.97 and self.gear < 6:
                self.gear_warn="Shift UP  Q ▲"
                self.gear_warn_t=50
        if self.gear_warn_t > 0:
            self.gear_warn_t -= 1
        else:
            self.gear_warn=""

        # ── engine force with inertia ──
        raw_engine=0.0
        if self.vx>=0:
            if accel and not brake:
                raw_engine=self._torque(speed,self.gear)*self.throttle
            if rev and speed<0.3:
                raw_engine=-self.REVERSE_FORCE
        else:
            if rev:   raw_engine=-self._torque(speed,self.gear)*0.55
            if accel: raw_engine=self.BRAKE_FORCE
        # engine inertia (sluggish response)
        self._engine_out+=(raw_engine-self._engine_out)*0.18
        net=self._engine_out

        # ── resistance forces ──
        net-=self.ROLL_RESIST*self.vx
        net-=self.AIR_DRAG*self.vx*speed

        # ── brake force with weight transfer ──
        if brake and speed>0.05:
            # weight transfer reduces rear grip when braking
            grip_factor=1.0+self.WEIGHT_TRANSFER*(speed/self.TOP_SPEED)
            net-=sign*self.BRAKE_FORCE*grip_factor

        # ── integrate velocity ──
        self.vx += net
        # Enforce per-gear speed cap — this is what makes gears work:
        # higher gear = higher cap = car accelerates further
        _,g_cap = GEARS[self.gear]
        if self.vx > g_cap:
            # slight bleed so downshift feels like engine braking not wall
            self.vx = g_cap + (self.vx - g_cap) * 0.85
        self.vx = max(-self.REVERSE_MAX, self.vx)
        if abs(self.vx) < 0.015: self.vx = 0.0

        # ── steering with speed-sensitive response ──
        # Steering authority reduces at high speed (realistic)
        steer_factor=max(0.4,1.0-speed/self.TOP_SPEED*0.5)
        if up_s:   self.vy=max(self.vy-self.STEER_ACCEL*steer_factor,-self.STEER_MAX)
        elif dn_s: self.vy=min(self.vy+self.STEER_ACCEL*steer_factor, self.STEER_MAX)
        else:      self.vy*=self.STEER_DAMP

        # ── move ──
        self.x+=self.vx; self.y+=self.vy
        self.y=max(ROAD_TOP+self.h//2,min(ROAD_BOT-self.h//2,self.y))
        self.on_road=ROAD_TOP<self.y<ROAD_BOT

        # ── fuel ──
        self.fuel=max(0.0,self.fuel-(self.throttle*.007+.0008))

        # ── skid marks on hard brake ──
        if brake and speed>2.2:
            add_skid(self.x,self.y+self.h//2-3,10)
            add_skid(self.x,self.y-self.h//2+3,10)

        # ── engine sound (rpm = f(speed, gear)) ──
        _,g_mx=GEARS[self.gear]
        rpm_norm=speed/max(g_mx,0.1)
        rpm_idx=int(rpm_norm*10 + self.gear*1.5)
        play_engine(rpm_idx)

    def speed_px(self): return self.vx
    def draw(self,surf,cam_x):
        sx=int(self.x-cam_x); sy=int(self.y)
        keys=pygame.key.get_pressed()
        brk=keys[pygame.K_SPACE] and self.vx>0.5
        draw_car(surf,self.model,self.color,sx,sy,self.w,self.h,
                 braking=brk,headlights=weather.mode=="night")
        if self.vx>SPEED_LIMIT:
            s=pygame.Surface((8,self.h-6),pygame.SRCALPHA)
            s.fill((*C_RED,150)); surf.blit(s,(sx+self.w//2-10,sy-self.h//2+3))
    def get_rect(self,cam_x):
        return pygame.Rect(int(self.x-cam_x)-self.w//2,int(self.y)-self.h//2,self.w,self.h)

# ── FLOAT MSG ─────────────────────────────────────────
class FloatMsg:
    def __init__(self,text,color,x,y):
        self.text=text; self.color=color; self.x=x; self.y=float(y)
        self.life=100; self.max_life=100
    def update(self): self.life-=1
    def alive(self): return self.life>0
    def draw(self,surf):
        a=int(255*self.life/self.max_life); rise=(self.max_life-self.life)*.65
        s=F_MED.render(self.text,True,self.color); s.set_alpha(a)
        surf.blit(s,s.get_rect(center=(self.x,self.y-rise)))

# ── ROAD SIGN ─────────────────────────────────────────
class RoadSign:
    TYPES=["speed_limit","warning","no_horn","give_way"]
    def __init__(self,x): self.x=x; self.type=random.choice(self.TYPES)
    def draw(self,surf,cam_x):
        sx=int(self.x-cam_x)
        if sx<-60 or sx>W+60: return
        pygame.draw.rect(surf,(120,120,130),(sx-2,ROAD_TOP-75,4,75))
        if self.type=="speed_limit":
            pygame.draw.circle(surf,C_WHITE,(sx,ROAD_TOP-80),18)
            pygame.draw.circle(surf,C_RED,(sx,ROAD_TOP-80),18,3)
            txt(surf,"50",F_SM,C_BLACK,sx,ROAD_TOP-80)
        elif self.type=="warning":
            pts=[(sx,ROAD_TOP-98),(sx-18,ROAD_TOP-64),(sx+18,ROAD_TOP-64)]
            pygame.draw.polygon(surf,C_AMBER,pts); pygame.draw.polygon(surf,C_BLACK,pts,2)
            txt(surf,"!",F_SM,C_BLACK,sx,ROAD_TOP-78)
        elif self.type=="no_horn":
            pygame.draw.circle(surf,C_WHITE,(sx,ROAD_TOP-80),18)
            pygame.draw.circle(surf,C_RED,(sx,ROAD_TOP-80),18,3)
            pygame.draw.line(surf,C_RED,(sx-12,ROAD_TOP-92),(sx+12,ROAD_TOP-68),3)
        elif self.type=="give_way":
            pts=[(sx,ROAD_TOP-64),(sx-18,ROAD_TOP-96),(sx+18,ROAD_TOP-96)]
            pygame.draw.polygon(surf,C_WHITE,pts); pygame.draw.polygon(surf,C_RED,pts,3)
            txt(surf,"↓",F_SM,C_RED,sx,ROAD_TOP-78)

# ── LAMP POST ─────────────────────────────────────────
class LampPost:
    def __init__(self,x,side): self.x=x; self.side=side
    def draw(self,surf,cam_x):
        sx=int(self.x-cam_x)
        if sx<-30 or sx>W+30: return
        py=FOOT_TOP_Y+2 if self.side=="top" else FOOT_BOT_Y-2
        ay=py-68 if self.side=="top" else py+68
        pygame.draw.rect(surf,(100,105,115),(sx-3,min(py,ay),5,abs(ay-py)+5))
        pygame.draw.line(surf,(100,105,115),(sx,ay),(sx+20,ay),3)
        lc=(255,240,180) if weather.mode=="night" else (200,200,180)
        pygame.draw.ellipse(surf,lc,(sx+12,ay-8,16,10))
        if weather.mode=="night":
            g=pygame.Surface((60,60),pygame.SRCALPHA)
            pygame.draw.circle(g,(255,240,160,45),(30,30),30); surf.blit(g,(sx+20-30,ay+10-30))

# ── INFINITE WORLD CHUNK ──────────────────────────────
# World is procedurally extended in chunks as player moves
CHUNK_SIZE=800   # world units per chunk

class WorldChunk:
    """One chunk of the infinite road."""
    def __init__(self,chunk_id):
        self.id=chunk_id
        base=chunk_id*CHUNK_SIZE
        # traffic light
        lx=base+random.randint(200,600)
        self.light=TrafficLight(lx)
        self.zebra=ZebraCrossing(lx+32,self.light)
        # trees
        self.trees=[base+random.randint(0,CHUNK_SIZE) for _ in range(8)]
        # road sign
        self.sign=RoadSign(base+random.randint(50,300)) if chunk_id>0 else None
        # lamps
        self.lamps=[]
        for x in range(base,base+CHUNK_SIZE,220):
            self.lamps.append(LampPost(x,"top"))
            self.lamps.append(LampPost(x+110,"bottom"))
        # clouds
        self.clouds=[(base+random.randint(0,CHUNK_SIZE),random.randint(25,130)) for _ in range(4)]

    def update(self):
        self.light.update(); self.zebra.update()

    def draw(self,surf,cam_x):
        # trees
        for wx in self.trees:
            sx=int(wx-cam_x)
            if -40<sx<W+40:
                pygame.draw.rect(surf,(90,55,25),(sx-5,FOOT_BOT_Y+12,10,22))
                pygame.draw.circle(surf,(35,115,35),(sx,FOOT_BOT_Y+6),23)
                pygame.draw.circle(surf,(55,155,55),(sx,FOOT_BOT_Y-2),17)
        for l in self.lamps: l.draw(surf,cam_x)
        if self.sign: self.sign.draw(surf,cam_x)
        self.zebra.draw(surf,cam_x)
        self.light.draw(surf,cam_x)
        # clouds
        if weather.mode!="night":
            al=180 if weather.mode=="clear" else 110
            for wx,wy in self.clouds:
                sx=int(wx-cam_x)
                if -60<sx<W+60:
                    for dx,dy,r in [(-28,0,24),(0,0,32),(28,0,24),(14,-14,20)]:
                        s=pygame.Surface((r*2,r*2),pygame.SRCALPHA)
                        pygame.draw.circle(s,(255,255,255,al),(r,r),r); surf.blit(s,(sx+dx-r,wy+dy-r))

# ── GAME WORLD (infinite) ─────────────────────────────
class GameWorld:
    NPC_POOL_SIZE=18

    def __init__(self,car_model,car_color,car_w,car_h):
        self.cam_x=0.0
        self.player=PlayerCar(car_model,car_color,car_w,car_h)
        self.score=0; self.penalties=0
        self.messages=[]; self.pen_cd=0
        self.elapsed=0; self.game_over=False
        self.distance_m=0   # metres driven
        self.best_dist=0

        # infinite chunk management
        self.chunks={}   # chunk_id -> WorldChunk
        self._ensure_chunks()

        # NPC pool — recycled as player moves forward
        self.npcs=[]
        for _ in range(self.NPC_POOL_SIZE):
            lane=random.choice([0,1])
            x=random.randint(300,3000)
            self.npcs.append(NPCCar(x,lane,random.uniform(1.4,3.2)))

        self.score_timer=0
        self.tip_scroll=W; self.elapsed_tip=0
        ai.request_tip("player just started driving")

        # score multiplier — increases with distance
        self.multiplier=1.0

    def _ensure_chunks(self):
        """Keep 3 chunks ahead and 1 behind loaded."""
        cur=int(self.player.x//CHUNK_SIZE)
        for cid in range(max(0,cur-1),cur+4):
            if cid not in self.chunks:
                self.chunks[cid]=WorldChunk(cid)
        # unload far-behind chunks
        for cid in list(self.chunks.keys()):
            if cid<cur-2: del self.chunks[cid]

    def _all_lights(self):
        return [c.light for c in self.chunks.values()]
    def _all_zebras(self):
        return [c.zebra for c in self.chunks.values()]

    def add_msg(self,text,color,wx=None):
        x=W//2 if wx is None else int(wx-self.cam_x)
        self.messages.append(FloatMsg(text,color,x,ROAD_TOP-30))

    def check_violations(self):
        if self.pen_cd>0: self.pen_cd-=1; return
        p=self.player; spd=p.speed_px()

        if spd>SPEED_LIMIT+0.5:
            self.penalties+=1; self.score=max(0,self.score-15)
            self.add_msg("🚨 SPEEDING! -15",C_PENALTY); self.pen_cd=80
            ai.request_tip("player is speeding"); return

        for lt in self._all_lights():
            if lt.is_red() and abs(p.x-lt.stop_x())<12 and spd>0.5:
                self.penalties+=1; self.score=max(0,self.score-40)
                self.add_msg("🛑 RED LIGHT! -40",C_PENALTY); self.pen_cd=110
                ai.request_tip("player ran a red light"); return

        for z in self._all_zebras():
            for ped in z.peds:
                if p.get_rect(self.cam_x).colliderect(ped.get_rect(self.cam_x)) and spd>0.3:
                    self.penalties+=1; self.score=max(0,self.score-80)
                    self.add_msg("⚠️ HIT PEDESTRIAN! -80",C_PENALTY); self.pen_cd=140
                    ai.request_tip("player hit a pedestrian"); return

        # NPC collision → GAME OVER
        for npc in self.npcs:
            if p.get_rect(self.cam_x).colliderect(npc.get_rect(self.cam_x)) and spd>1.0:
                self.game_over=True
                self.add_msg("💥 COLLISION! GAME OVER",C_PENALTY); return

        if self.player.fuel<=0:
            self.game_over=True; self.add_msg("⛽ OUT OF FUEL! GAME OVER",C_PENALTY)

    def check_rewards(self):
        self.score_timer+=1
        spd=self.player.speed_px()
        # passive score for driving (distance-based, faster = more)
        if self.score_timer>=30:
            self.score_timer=0
            if spd>0.5:
                pts=int(2*self.multiplier*(spd/SPEED_LIMIT))
                self.score+=pts
        # multiplier grows with distance
        self.multiplier=1.0+self.distance_m/2000.0
        # milestone bonuses every 500m
        if int(self.distance_m)%500<2 and self.distance_m>10:
            m=int(self.distance_m//500)
            if not hasattr(self,f'_ms{m}'):
                setattr(self,f'_ms{m}',True)
                bonus=int(100*self.multiplier)
                self.score+=bonus
                self.add_msg(f"🏅 {int(self.distance_m)}m! +{bonus} pts",C_SCORE_C)

    def update(self,keys):
        if self.game_over: return
        self.elapsed+=1
        self.player.update(keys)
        self.cam_x=max(0,self.player.x-W*.32)
        self.distance_m=self.player.x/6.0   # scale px→metres
        if self.distance_m>self.best_dist: self.best_dist=self.distance_m

        weather.update(); update_skids(); ai.tick()
        self._ensure_chunks()

        for c in self.chunks.values(): c.update()

        # recycle NPCs that fall too far behind
        for npc in self.npcs:
            npc.update(self._all_lights(),self.npcs)
            if npc.x<self.player.x-400:
                npc.x=self.player.x+random.randint(400,1200)
                npc.lane=random.choice([0,1]); npc.y=ROAD_TOP+npc.lane*LANE_H+LANE_H//2
                npc.speed=random.uniform(1.4,3.2); npc._cs=npc.speed

        self.check_violations(); self.check_rewards()

        for msg in self.messages[:]:
            msg.update()
            if not msg.alive(): self.messages.remove(msg)

        self.tip_scroll-=1.5
        self.elapsed_tip+=1
        if self.tip_scroll<-len(ai.tip)*9:
            self.tip_scroll=W
            ctx=f"{int(self.player.vx*18)}km/h gear {self.player.gear} {weather.mode} score {self.score} dist {int(self.distance_m)}m"
            ai.request_tip(ctx)

    # ── DRAW ──────────────────────────────────────────
    def draw_bg(self):
        tc,bc=weather.sky()
        # Sky — only up to where footpath starts
        for row in range(FOOT_TOP_Y):
            pygame.draw.line(screen,lerp_c(tc,bc,row/max(FOOT_TOP_Y,1)),(0,row),(W,row))
        # Grass — only below where footpath ends
        for row in range(FOOT_BOT_Y,H):
            pygame.draw.line(screen,lerp_c(C_GRASS,C_GRASS_D,(row-FOOT_BOT_Y)/(H-FOOT_BOT_Y)),(0,row),(W,row))

        # ── TOP FOOTPATH ── (FOOT_TOP_Y → ROAD_TOP)
        pygame.draw.rect(screen,C_SIDEWALK,(0,FOOT_TOP_Y,W,ROAD_TOP-FOOT_TOP_Y))
        # scrolling pavement tile lines
        tile=52
        off=int(self.cam_x*0.12)%tile
        for tx in range(-tile,W+tile,tile):
            pygame.draw.line(screen,(150,130,105),(tx-off,FOOT_TOP_Y),(tx-off,ROAD_TOP-3),1)
        pygame.draw.line(screen,(150,130,105),(0,FOOT_TOP_Y+(ROAD_TOP-FOOT_TOP_Y)//2),(W,FOOT_TOP_Y+(ROAD_TOP-FOOT_TOP_Y)//2),1)
        # kerb — dark strip right above road
        pygame.draw.rect(screen,(70,65,60),(0,ROAD_TOP-3,W,4))

        # ── BOTTOM FOOTPATH ── (ROAD_BOT → FOOT_BOT_Y)
        pygame.draw.rect(screen,C_SIDEWALK,(0,ROAD_BOT,W,FOOT_BOT_Y-ROAD_BOT))
        # scrolling tile lines
        for tx in range(-tile,W+tile,tile):
            pygame.draw.line(screen,(150,130,105),(tx-off,ROAD_BOT+3),(tx-off,FOOT_BOT_Y),1)
        pygame.draw.line(screen,(150,130,105),(0,ROAD_BOT+(FOOT_BOT_Y-ROAD_BOT)//2),(W,ROAD_BOT+(FOOT_BOT_Y-ROAD_BOT)//2),1)
        # kerb — dark strip right below road
        pygame.draw.rect(screen,(70,65,60),(0,ROAD_BOT-1,W,4))

    def draw_road(self):
        pygame.draw.rect(screen,weather.road_col(),(0,ROAD_TOP,W,ROAD_BOT-ROAD_TOP))
        for row in range(ROAD_TOP,ROAD_BOT,3):
            pygame.draw.line(screen,C_ASPH_L,(0,row),(W,row))
        dw,gap=28,18; off=int(self.cam_x)%(dw+gap); x=-off
        while x<W:
            pygame.draw.rect(screen,C_LANE,(x,ROAD_MID-3,dw,6),border_radius=2); x+=dw+gap
        pygame.draw.rect(screen,C_LANE_SOL,(0,ROAD_TOP,W,3))
        pygame.draw.rect(screen,C_LANE_SOL,(0,ROAD_BOT-3,W,3))
        # Guardrail posts on top kerb edge
        for i in range(-1,W//60+2):
            rx=i*60-(int(self.cam_x*.4)%60)
            pygame.draw.rect(screen,(80,85,90),(rx,ROAD_TOP-30,5,30))
            pygame.draw.rect(screen,C_GUARDRAIL,(rx-10,ROAD_TOP-24,25,6),border_radius=2)
        # Guardrail posts on bottom kerb edge
        for i in range(-1,W//60+2):
            rx=i*60-(int(self.cam_x*.4)%60)+30
            pygame.draw.rect(screen,(80,85,90),(rx,ROAD_BOT+1,5,28))
            pygame.draw.rect(screen,C_GUARDRAIL,(rx-10,ROAD_BOT+6,25,6),border_radius=2)
        if weather.mode=="rain":
            for x in range(0,W,60):
                r=pygame.Surface((50,4),pygame.SRCALPHA); r.fill((200,220,255,35)); screen.blit(r,(x,ROAD_MID-2))
        draw_skids(screen,self.cam_x)

    def draw_hud(self):
        p=self.player; spd=max(0.0,p.speed_px())
        spd_kmh=int(spd*18); lim_kmh=int(SPEED_LIMIT*18)
        spd_col=C_RED if spd>SPEED_LIMIT else C_GREEN

        # ── score / distance / multiplier ──
        rr(screen,C_HUD_BG,(10,10,290,100),r=14,a=225)
        txt(screen,"SCORE",F_SM,(140,140,160),58,26)
        txt(screen,f"{self.score}",F_BIG,C_SCORE_C,58,53,shadow=True)
        txt(screen,"DIST",F_SM,(140,140,160),175,26)
        txt(screen,f"{int(self.distance_m)}m",F_BIG,C_WHITE,175,53,shadow=True)
        # multiplier badge
        mc=(255,220,50) if self.multiplier>1.5 else (200,200,200)
        txt(screen,f"×{self.multiplier:.1f}",F_SM,mc,255,38)
        txt(screen,f"BEST {int(HIGH_SCORE)}",F_SM,(120,180,255),255,58)

        # ── weather badge ──
        wi={"clear":"☀️","rain":"🌧️","night":"🌙"}[weather.mode]
        rr(screen,C_HUD_BG,(W-115,10,105,36),r=10,a=200)
        txt(screen,wi+" "+weather.mode.upper(),F_SM,C_WHITE,W-62,28)

        # ── fuel bar ──
        rr(screen,C_HUD_BG,(10,H-50,200,38),r=8,a=215)
        txt(screen,"⛽",F_SM,C_WHITE,28,H-31)
        fw=int(168*p.fuel/100)
        fc=(255,80,50) if p.fuel<20 else (255,180,50) if p.fuel<50 else C_GREEN
        rr(screen,(40,40,55),(42,H-44,160,26),r=5,a=200)
        if fw>0: rr(screen,fc,(42,H-44,fw,26),r=5)
        txt(screen,f"{int(p.fuel)}%",F_SM,C_WHITE,42+80,H-31)

        # ── gear panel ──
        rr(screen,C_HUD_BG,(W-235,H-60,225,50),r=10,a=225)
        for g in range(1,7):
            gx=W-230+g*34; gy=H-54
            is_active=(g==p.gear)
            gc=(255,210,40) if is_active else (40,40,65)
            rr(screen,gc,(gx-15,gy,30,38),r=6,a=240)
            txt(screen,str(g),F_MED,C_BLACK if is_active else (100,100,130),gx,gy+19)
        txt(screen,"Q▲",F_SM,(80,220,80),W-232,H-31)
        txt(screen,"E▼",F_SM,(220,80,80),W-232+7*34,H-31)

        # gear warning
        if p.gear_warn_t>0 and p.gear_warn:
            wc=C_RED if "UP" in p.gear_warn else C_AMBER
            txt(screen,p.gear_warn,F_BIG,wc,W//2,ROAD_TOP-55,shadow=True)

        # ── speedometer arc ──
        sc_x,sc_y,sc_r=W-105,H-105,80
        sd=pygame.Surface((sc_r*2+14,sc_r*2+14),pygame.SRCALPHA)
        pygame.draw.circle(sd,(12,12,18,218),(sc_r+7,sc_r+7),sc_r+6)
        screen.blit(sd,(sc_x-sc_r-7,sc_y-sc_r-7))
        sa,sw=210,240; lr=(SPEED_LIMIT*18)/(p.TOP_SPEED*18)
        def arc(c,a0,a1,th=7):
            steps=max(2,abs(int(a1-a0)))
            for i in range(steps):
                t0=math.radians(a0+i); t1=math.radians(a0+i+1)
                pygame.draw.line(screen,c,
                    (int(sc_x+(sc_r-8)*math.cos(t0)),int(sc_y-(sc_r-8)*math.sin(t0))),
                    (int(sc_x+(sc_r-8)*math.cos(t1)),int(sc_y-(sc_r-8)*math.sin(t1))),th)
        arc((50,50,65),-sa,-(sa-sw),8)
        se=sa-lr*sw; arc((38,185,75),-sa,-se,8); arc((200,45,45),-se,-(sa-sw),8)
        ratio=min(1.0,spd/p.TOP_SPEED); na=math.radians(sa-ratio*sw)
        r8=sc_r-8
        pygame.draw.line(screen,C_WHITE,(sc_x,sc_y),(int(sc_x+r8*math.cos(na)),int(sc_y-r8*math.sin(na))),3)
        pygame.draw.circle(screen,(220,220,230),(sc_x,sc_y),5)
        txt(screen,f"{spd_kmh}",F_BIG,spd_col,sc_x,sc_y+22,shadow=True)
        txt(screen,"km/h",F_SM,(150,150,165),sc_x,sc_y+40)
        txt(screen,f"MAX {lim_kmh}",F_SM,C_RED,sc_x,sc_y-sc_r+10)

        # throttle bar
        tb_x,tb_y,tb_w,tb_h=W-208,H-165,14,108
        rr(screen,(28,28,40),(tb_x-2,tb_y-2,tb_w+4,tb_h+4),r=5,a=215)
        fh=int(tb_h*p.throttle)
        if fh>0: rr(screen,(255,int(200*(1-p.throttle)),30),(tb_x,tb_y+tb_h-fh,tb_w,fh),r=4)
        txt(screen,"THR",F_SM,(120,120,140),tb_x+tb_w//2,tb_y-10)

        # ── mini distance bar ──
        mm_x,mm_y,mm_w,mm_h=W//2-180,12,360,22
        rr(screen,C_HUD_BG,(mm_x-2,mm_y-2,mm_w+4,mm_h+4),r=6,a=200)
        pygame.draw.rect(screen,(40,40,55),(mm_x,mm_y,mm_w,mm_h))
        # infinite bar — show last 5km
        vis=min(1.0,(self.distance_m%5000)/5000)
        pygame.draw.rect(screen,(38,185,75),(mm_x,mm_y,int(mm_w*vis),mm_h))
        pygame.draw.circle(screen,(255,255,100),(mm_x+int(mm_w*vis),mm_y+mm_h//2),6)
        for npc in self.npcs:
            rel=(npc.x-self.player.x)/1000.0
            if 0<rel<1:
                nx2=mm_x+mm_w//2+int(rel*mm_w//2)
                if mm_x<nx2<mm_x+mm_w: pygame.draw.circle(screen,(255,100,100),(nx2,mm_y+mm_h//2),3)
        txt(screen,f"{int(self.distance_m)}m",F_SM,C_WHITE,mm_x+mm_w//2,mm_y+mm_h+12)

        # ── AI ticker ──
        tip_str="💡 AI COACH: "+ai.tip+("  [loading…]" if ai.loading else "")
        rr(screen,C_HUD_BG,(0,H-26,W,26),r=0,a=215)
        ts=F_MONO.render(tip_str,True,(180,230,255))
        screen.blit(ts,(int(self.tip_scroll),H-22))

        hint="→/← Throttle/Rev  |  SPACE Brake  |  W/S Steer  |  Q Gear▲  E Gear▼"
        screen.blit(F_SM.render(hint,True,(85,85,108)),
                    F_SM.render(hint,True,(85,85,108)).get_rect(bottomleft=(12,H-28)))

        # AI spinner
        if ai.loading:
            ang=(self.elapsed*6)%360
            pygame.draw.arc(screen,(100,200,255),(W-30,H-30,18,18),
                            math.radians(ang),math.radians(ang+270),3)

    def draw(self):
        self.draw_bg()
        for c in sorted(self.chunks.values(),key=lambda x:x.id):
            # draw clouds & lamps & signs from chunk
            c.draw(screen,self.cam_x)
        self.draw_road()
        for npc in self.npcs: npc.draw(screen,self.cam_x)
        self.player.draw(screen,self.cam_x)
        weather.draw_fx(screen)
        self.draw_hud()
        for msg in self.messages: msg.draw(screen)

# ── CAR SELECTION ─────────────────────────────────────
def car_select_screen():
    selected=0; t=0
    PREV_W,PREV_H=140,80
    while True:
        clock.tick(FPS)
        for ev in pygame.event.get():
            if ev.type==pygame.QUIT: pygame.quit(); sys.exit()
            if ev.type==pygame.KEYDOWN:
                if ev.key==pygame.K_ESCAPE: pygame.quit(); sys.exit()
                if ev.key==pygame.K_LEFT:  selected=(selected-1)%len(CAR_CATALOGUE)
                if ev.key==pygame.K_RIGHT: selected=(selected+1)%len(CAR_CATALOGUE)
                if ev.key in(pygame.K_RETURN,pygame.K_SPACE): return CAR_CATALOGUE[selected]
        t+=1
        for row in range(H):
            f=row/H; pygame.draw.line(screen,lerp_c((8,8,20),(20,25,45),f),(0,row),(W,row))
        txt(screen,"SELECT YOUR CAR",F_TITLE,(240,200,50),W//2,60,shadow=True)
        txt(screen,"← → to browse  |  ENTER to confirm",F_MED,(180,180,210),W//2,108)
        cols=4
        for i,(mid,label,color,cw,ch,desc) in enumerate(CAR_CATALOGUE):
            row=i//cols; col=i%cols
            bx=W//2-cols*78+col*156; by=200+row*140
            is_sel=(i==selected)
            rr(screen,(40,60,100) if is_sel else (20,22,35),(bx-10,by-10,PREV_W+20,PREV_H+40),r=12,a=230)
            bc2=(255,220,50) if is_sel else (50,55,80)
            pygame.draw.rect(screen,bc2,(bx-10,by-10,PREV_W+20,PREV_H+40),2,border_radius=12)
            pygame.draw.rect(screen,(48,48,52),(bx,by+PREV_H//2-14,PREV_W,28))
            pygame.draw.rect(screen,(220,220,60),(bx+10,by+PREV_H//2-2,PREV_W-20,4))
            draw_car(screen,mid,color,bx+PREV_W//2,by+PREV_H//2,min(cw,100),ch)
            lc=(255,220,50) if is_sel else (180,180,200)
            txt(screen,label,F_SM,lc,bx+PREV_W//2,by+PREV_H+8)
        mid,label,color,cw,ch,desc=CAR_CATALOGUE[selected]
        rr(screen,(15,18,35),(W//2-280,H-120,560,90),r=14,a=230)
        txt(screen,label,F_BIG,color,W//2,H-100,shadow=True)
        txt(screen,desc,F_MED,(200,210,230),W//2,H-68)
        txt(screen,"Press ENTER to drive!",F_SM,(140,220,140),W//2,H-44)
        pulse=int(5*math.sin(t*.08))
        pygame.draw.rect(screen,(255,220,50),(
            W//2-cols*78+(selected%cols)*156-10+pulse,
            200+(selected//cols)*140-10+pulse,
            PREV_W+20-pulse*2,PREV_H+40-pulse*2),2,border_radius=12)
        pygame.display.flip()

# ── TITLE SCREEN ──────────────────────────────────────
def title_screen():
    t=0; ai.request_tip("player on title screen")
    while True:
        clock.tick(FPS)
        for ev in pygame.event.get():
            if ev.type==pygame.QUIT: pygame.quit(); sys.exit()
            if ev.type==pygame.KEYDOWN:
                if ev.key in(pygame.K_RETURN,pygame.K_SPACE): return
                if ev.key==pygame.K_ESCAPE: pygame.quit(); sys.exit()
        t+=1
        for row in range(H):
            f=row/H; rc=int(8+20*f+8*math.sin(t*.018+f*3))
            pygame.draw.line(screen,(rc,int(12+22*f),int(28+38*f)),(0,row),(W,row))
        pygame.draw.rect(screen,(40,40,46),(0,H//2+55,W,90))
        off=t%50
        for i in range(-1,W//50+2):
            pygame.draw.rect(screen,C_LANE,(i*50+off-25,H//2+98,26,6),border_radius=2)
        ts1=F_TITLE.render("ROAD SAFETY",True,(240,70,45))
        ts2=F_TITLE.render("SIMULATOR v4",True,(240,195,45))
        screen.blit(ts1,ts1.get_rect(center=(W//2,H//2-70)))
        screen.blit(ts2,ts2.get_rect(center=(W//2,H//2-10)))
        txt(screen,"Infinite Road  ·  Manual Gears  ·  AI Coach  ·  High Score",F_MED,(180,180,210),W//2,H//2+42)
        if(t//28)%2==0: txt(screen,"▶  PRESS SPACE TO START  ◀",F_MED,C_WHITE,W//2,H//2+88)
        hs=load_hs()
        if hs>0:
            txt(screen,f"🏆 HIGH SCORE: {hs} pts",F_MED,(255,220,50),W//2,H//2+118)
        rules=[("🚦","Obey traffic lights",C_RED),("🦶","Don't hit pedestrians",C_WHITE),
               ("💥","Avoid NPC cars — collision = GAME OVER",C_PENALTY),
               ("⚙️","Q = Gear Up   E = Gear Down",(100,200,255)),
               ("⛽","Don't run out of fuel",C_AMBER)]
        rr(screen,(18,18,28),(W//2-260,H-185,520,130),r=14,a=210)
        for i,(ic,rule,col) in enumerate(rules):
            rs=F_SM.render(f"{ic}  {rule}",True,col); screen.blit(rs,rs.get_rect(center=(W//2,H-178+i*23)))
        pygame.display.flip()

# ── GAME OVER SCREEN ──────────────────────────────────
def game_over_screen(score,distance,elapsed,new_hs):
    global HIGH_SCORE
    t=0; tip_x=W
    ai.refresh(f"player crashed, score {score}, distance {int(distance)}m, time {elapsed//FPS}s")
    while True:
        clock.tick(FPS)
        for ev in pygame.event.get():
            if ev.type==pygame.QUIT: pygame.quit(); sys.exit()
            if ev.type==pygame.KEYDOWN:
                if ev.key in(pygame.K_RETURN,pygame.K_SPACE,pygame.K_r): return
                if ev.key==pygame.K_ESCAPE: pygame.quit(); sys.exit()
        t+=1; tip_x-=1.2
        if tip_x<-len(ai.tip)*9: tip_x=W
        for row in range(H):
            f=row/H
            pygame.draw.line(screen,(int(28+18*f),int(8+8*f),int(8+8*f)),(0,row),(W,row))

        txt(screen,"💥 COLLISION!",F_TITLE,C_RED,W//2,H//2-120,shadow=True)
        txt(screen,"Your journey ends here.",F_MED,(255,160,160),W//2,H//2-68)

        rr(screen,(18,18,28),(W//2-230,H//2-35,460,160),r=14,a=230)
        txt(screen,f"Score      :  {score}",F_BIG,C_SCORE_C,W//2,H//2)
        txt(screen,f"Distance   :  {int(distance)} m",F_BIG,C_WHITE,W//2,H//2+40)
        mins=elapsed//(FPS*60); secs=(elapsed//FPS)%60
        txt(screen,f"Survived   :  {mins:02d}:{secs:02d}",F_BIG,C_WHITE,W//2,H//2+80)

        if new_hs:
            pulse=int(5*abs(math.sin(t*.08)))
            txt(screen,"🏆 NEW HIGH SCORE!",F_BIG,(255,220,50),W//2,H//2-52,shadow=True)

        grade="S" if score>2000 else "A" if score>1000 else "B" if score>500 else "C"
        gc={"S":(100,255,120),"A":(100,200,255),"B":(255,220,50),"C":(255,100,50)}[grade]
        txt(screen,f"Grade: {grade}",F_BIG,gc,W//2,H//2-90)

        if(t//28)%2==0: txt(screen,"▶  SPACE / R to Play Again  ◀",F_MED,(200,200,255),W//2,H//2+128)

        rr(screen,(12,12,20),(0,H-26,W,26),r=0,a=205)
        screen.blit(F_MONO.render("🤖 AI Coach: "+ai.tip,True,(160,220,255)),(int(tip_x),H-22))
        pygame.display.flip()

# ── MAIN ──────────────────────────────────────────────
def main():
    global HIGH_SCORE
    while True:
        title_screen()
        car_data=car_select_screen()
        mid,label,color,cw,ch,desc=car_data
        world=GameWorld(mid,color,cw,ch)
        running=True
        while running:
            clock.tick(FPS)
            for ev in pygame.event.get():
                if ev.type==pygame.QUIT: pygame.quit(); sys.exit()
                if ev.type==pygame.KEYDOWN:
                    if ev.key==pygame.K_ESCAPE: running=False
            world.update(pygame.key.get_pressed())
            world.draw(); pygame.display.flip()
            if world.game_over:
                pygame.time.wait(500)
                new_hs=world.score>HIGH_SCORE
                if new_hs:
                    HIGH_SCORE=world.score; save_hs(HIGH_SCORE)
                game_over_screen(world.score,world.distance_m,world.elapsed,new_hs)
                break

if __name__=="__main__":
    main()
