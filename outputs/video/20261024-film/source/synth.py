"""Original music + sound design synthesizer for Magnm films (no third-party audio).
score(events, total, bpm) -> stereo float array at 48k."""
import numpy as np
from scipy.signal import butter, sosfilt
SR = 48000
def t_(d): return np.arange(int(d*SR))/SR
def env(n, a=0.005, r=0.2, sus=None):
    e = np.ones(n); A = max(1,int(a*SR)); e[:A] = np.linspace(0,1,A)
    if sus is None:
        e[A:] = np.exp(-np.arange(n-A)/(r*SR))
    else:
        R = max(1,int(r*SR)); e[-R:] *= np.linspace(1,0,R)
    return e
def lp(x,f,o=2): return sosfilt(butter(o,f,'low',fs=SR,output='sos'),x)
def hp(x,f,o=2): return sosfilt(butter(o,f,'high',fs=SR,output='sos'),x)
def bp(x,lo,hi,o=2): return sosfilt(butter(o,[lo,hi],'band',fs=SR,output='sos'),x)
def kick(gain=1.0):
    t=t_(0.45); f=48+90*np.exp(-t*28); ph=2*np.pi*np.cumsum(f)/SR
    return gain*np.sin(ph)*np.exp(-t*7.5)
def sub_drop(gain=1.0, d=1.6):
    t=t_(d); f=70*np.exp(-t*1.3)+28; ph=2*np.pi*np.cumsum(f)/SR
    return gain*np.tanh(1.6*np.sin(ph))*np.exp(-t*2.2)
def hat(gain=0.25, d=0.06, seed=0):
    rng=np.random.default_rng(seed); x=hp(rng.standard_normal(int(d*SR)),7000,4)
    return gain*x*env(len(x),0.001,d/3)
def clap(gain=0.4, seed=1):
    rng=np.random.default_rng(seed); x=bp(rng.standard_normal(int(0.25*SR)),900,4000)
    e=np.zeros(len(x))
    for o in (0,0.012,0.024): i=int(o*SR); e[i:]+=np.exp(-np.arange(len(x)-i)/(0.05*SR))
    return gain*x*e/3
def tone(freq, d, gain=0.2, kind='saw', detune=0.006, cutoff=1800, a=0.02, r=0.3):
    t=t_(d); out=np.zeros(len(t))
    for dt in (-detune,0,detune):
        f=freq*(1+dt)
        if kind=='saw': w=2*((t*f)%1)-1
        elif kind=='sq': w=np.sign(np.sin(2*np.pi*f*t))
        else: w=np.sin(2*np.pi*f*t)
        out+=w
    out=lp(out/3,cutoff)
    return gain*out*env(len(t),a,r,sus=True)
def pad(freqs, d, gain=0.12, cutoff=1200):
    return sum(tone(f,d,gain/len(freqs)*2,'saw',0.008,cutoff,a=min(1.2,d/3),r=min(1.5,d/3)) for f in freqs)
def riser(d, gain=0.3, seed=3):
    rng=np.random.default_rng(seed); n=int(d*SR); x=rng.standard_normal(n)
    out=np.zeros(n); seg=int(0.02*SR)
    for i in range(0,n,seg):
        fc=300+ (i/n)**2*9000
        out[i:i+seg]=bp(x[i:i+seg+0],max(100,fc*0.7),min(SR/2-100,fc*1.3),1)[:len(out[i:i+seg])]
    return gain*out*np.linspace(0,1,n)**2
def whoosh(d=0.5, gain=0.25, seed=4):
    rng=np.random.default_rng(seed); n=int(d*SR); x=lp(rng.standard_normal(n),2500)
    e=np.sin(np.linspace(0,np.pi,n))**2
    return gain*x*e
def tick(gain=0.15, f=2400):
    t=t_(0.03); return gain*np.sin(2*np.pi*f*t)*np.exp(-t*180)
def place(buf, x, at, pan=0.0):
    i=int(at*SR); j=min(len(buf), i+len(x))
    if j<=i: return
    l=np.cos((pan+1)*np.pi/4); r=np.sin((pan+1)*np.pi/4)
    buf[i:j,0]+=x[:j-i]*l*1.414; buf[i:j,1]+=x[:j-i]*r*1.414
def load(path):
    import subprocess
    raw=subprocess.check_output(["ffmpeg","-v","error","-i",path,"-f","f32le","-ac","1","-ar",str(SR),"-"])
    return np.frombuffer(raw,dtype=np.float32).astype(np.float64)
def master(buf, target_peak=0.89):
    buf=np.tanh(buf*1.1)/np.tanh(1.1)
    p=np.max(np.abs(buf)); return buf*(target_peak/p if p>0 else 1)
def write(buf, path):
    import subprocess
    pcm=(np.clip(buf,-1,1)*32767).astype(np.int16).tobytes()
    subprocess.run(["ffmpeg","-y","-v","error","-f","s16le","-ar",str(SR),"-ac","2","-i","-",path],input=pcm,check=True)
