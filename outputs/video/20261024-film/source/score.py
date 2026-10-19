import numpy as np, sys
sys.path.insert(0,'/home/claude/film')
from synth import *
TOTAL=46.875; B=0.625; E=B/2
N=int((TOTAL+0.5)*SR); mus=np.zeros((N,2)); sfx=np.zeros((N,2))
SFXD='/home/claude/stage/video-skills/magnm-video/assets/sfx'
kp=[load(f'{SFXD}/keyboard/keypress-{i:03d}.wav') for i in range(1,9)]
imp=[load(f'{SFXD}/impact/impactSoft_medium_00{i}.ogg') for i in range(5)]
uiclick=load(f'{SFXD}/ui/click2.ogg'); pop=load(f'{SFXD}/interface/bong_001.ogg'); tclick=load(f'{SFXD}/interface/click_003.ogg')
D1=36.71; D2=73.42; A2=110.0; F2=87.31; C3=130.81; D3=146.83
def rng_noise(d,seed=9): return np.random.default_rng(seed).standard_normal(int(d*SR))
# room tone
room=lp(rng_noise(TOTAL),900)*0.006; mus[:len(room),0]+=room; mus[:len(room),1]+=room
# 0-8.125 drone fade in
d=tone(D1*2,8.1,0.0,'sine')
t=t_(8.125); drone=(np.sin(2*np.pi*D2*t)*0.6+np.sin(2*np.pi*D2*1.5*t)*0.15)*np.linspace(0,1,len(t))**1.5*0.18
drone[int(5.0*SR):]*=1.25; drone[-int(0.03*SR):]*=np.linspace(1,0,int(0.03*SR))
place(mus,drone,0)
# build 9.375-23.125: filtered 8th pulse D minor arpeggio
notes=[D2,D2,F2,A2,D2,C3,A2,F2]
for k in range(int((23.125-9.375)/E)):
    at=9.375+k*E; f=notes[k%8]
    prog=(at-9.375)/13.75
    cut=500+prog**1.6*3200
    place(mus,tone(f,E*0.9,0.09,'saw',0.004,cut,a=0.004,r=0.08),at,pan=(-0.25 if k%2 else 0.25))
    if at>=11.875: place(mus,hat(0.05 if k%2 else 0.08,seed=k),at,pan=0.3)
    if at>=15.625 and k%2==0: place(mus,kick(0.55),at)
    # sub
    if k%4==0: place(mus,tone(D1*2 if (k//8)%2==0 else (F2/2),E*3.6,0.10,'sine',0,400,a=0.01,r=0.2),at)
place(mus,riser(0.625,0.18),22.5)
# burst 23.125-28.75
for k in range(int((26.875-23.125)/E)):
    at=23.125+k*E; f=notes[k%8]
    place(mus,tone(f*2,E*0.9,0.08,'saw',0.006,4200,a=0.003,r=0.07),at)
    place(mus,hat(0.09,seed=100+k),at,pan=-0.3)
    if k%2==0: place(mus,kick(0.7),at); place(mus,tone(D1*2,B*0.9,0.12,'saw',0.01,300,a=0.005,r=0.2),at)
    else: place(mus,clap(0.12,seed=k),at)
place(mus,sub_drop(0.6,2.2),26.875); place(mus,kick(0.9),26.875)
place(mus,lp(rng_noise(1.8,4),3000)*np.exp(-t_(1.8)*2.5)*0.08,26.875)
# drop / tension 28.75-35.0: sub + half-time pulse
for k in range(int((35.0-28.75)/B)):
    at=28.75+k*B
    place(mus,tone(D1*2,B*0.95,0.09,'sine',0,300,a=0.01,r=0.15),at)
    if k%2==1: place(mus,tone(A2,0.12,0.05,'saw',0.004,900,a=0.003,r=0.06),at)
    if at>=32.5: place(mus,tick(0.05,1800),at); place(mus,tick(0.03,1800),at+E)
# silence after 35.0 : hard cut music
cut=int(35.0*SR); mus[cut:int(37.5*SR)]=0; mus[cut:int(37.5*SR)]+= 0  # room removed too
# calm pad 37.5-43.75 Dmaj9
padv=pad([D2*2,146.83*2*1.26*0.5*2/2*1.0, 185.0, 220.0, 329.63],6.25,0.20,1400)
padv*=np.linspace(1,0.55,len(padv)); place(mus,padv,37.5)
place(mus,tone(587.33,1.2,0.07,'sine',0,3000,a=0.003,r=0.6),38.125)
# end resolve
place(mus,sub_drop(0.45,1.8),45.0); place(mus,tone(D2,1.85,0.12,'sine',0,600,a=0.01,r=1.2),45.0)
place(mus,tone(D2*2,1.85,0.05,'tri' ,0,1200,a=0.01,r=1.2),45.0)
# room tone back in for end
# --- SFX
def P(x,at,g=1.0,pan=0): place(sfx,x*g,at,pan)
P(kp[0],1.25,0.35)
P(imp[0],2.813,0.5); P(imp[3],5.938,0.6)
for i,at in enumerate(np.arange(5.0,5.6,0.05)): P(kp[i%8],at,0.08)
P(imp[2],9.375,0.55)
P(lp(rng_noise(0.6,5),1500)*np.sin(np.linspace(0,np.pi,int(0.6*SR)))*0.07,10.625)
P(uiclick,12.188,0.35); P(tone(1174.66,0.4,0.06,'sine',0,6000,a=0.002,r=0.2),13.125)
fold=lp(rng_noise(0.32,6),2200)*np.sin(np.linspace(0,np.pi,int(0.32*SR)))**2*0.10
P(fold,15.313,0.4); P(kp[4],16.367,0.45); P(tone(1567.98,0.35,0.05,'sine',0,7000,a=0.002,r=0.15),16.787)
P(pop,19.375,0.35)
for k,at in enumerate([23.125,23.75,24.375,25.0,25.625,26.25]): P(imp[k%5],at,0.4)
for k in range(6): P(tclick,23.4375+k*B,0.25)
P(imp[1],26.875,0.7)
P(uiclick,30.0,0.4)
P(kp[5],34.875,0.5); P(kp[6],34.938,0.5)
P(fold,37.25,0.32)
for i in range(4): P(kp[i+1],44.375+i*0.156,0.4)
P(imp[4],45.0,0.45)
P(tick(0.05,2400),41.25); P(tick(0.05,2400),41.875)
out=mus*0.9+sfx
out[int(TOTAL*SR):]=0
out=master(out[:int(TOTAL*SR)],0.89)
write(out,'/home/claude/film/score.wav')
print('ok')
