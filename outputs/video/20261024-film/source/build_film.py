#!/usr/bin/env python3
"""Magnm film 20261024 "See it before it runs." - builds HyperFrames compositions for 16:9 and 9:16.
Script: Magnm-Marketing/outputs/scripts/20261024-film_script (Writer v1 + Reviewer fixes)."""
import os, shutil, sys, html
ROOT = "/home/claude/film"
TOTAL = 46.875
B = 0.625; E = B / 2
STONE, INK, LIME, MUTED, PLATE = "#EFEFEC", "#0A0A0A", "#C8F31D", "#6B6B66", "#201F22"
TXT = {}  # filled from text.py so the Reviewer's fixes are one place
exec(open(f"{ROOT}/text.py").read())

def build(fmt):
    L = fmt == "desktop"
    W, H = (1920, 1080) if L else (1080, 1920)
    G = 120 if L else 64
    P = f"{ROOT}/proj/{fmt}"
    if os.path.exists(P): shutil.rmtree(P)
    shutil.copytree(f"{ROOT}/assets", f"{P}/assets", ignore=shutil.ignore_patterns("qf.png", "sf.png", "df.png"))
    for fn in ["package.json", "hyperframes.json", "meta.json"]:
        shutil.copy(f"/home/claude/video-work/magnm-intro/{fn}", f"{P}/{fn}")
    secs, clips, js = [], [], []
    def J(s): js.append(s)
    def sec(i, a, b, bg, inner, extra=""):
        secs.append(f'<section id="s{i}" class="clip sec" data-start="{a:.3f}" data-duration="{b-a:.3f}" data-track-index="1" style="background:{bg};{extra}">{inner}</section>')
    track = [10]
    def clip(tag, cid, src, a, b, x, y, w, h=None, z=5, style="", extra=""):
        track[0] += 1
        hs = f"height:{h}px;" if h else ""
        if tag == "video":
            clips.append(f'<video id="{cid}" class="clip" src="{src}" data-start="{a:.3f}" data-duration="{b-a:.3f}" data-track-index="{track[0]}" muted playsinline style="position:absolute;left:{x}px;top:{y}px;width:{w}px;{hs}z-index:{z};object-fit:cover;{style}"></video>')
        else:
            clips.append(f'<div id="{cid}" class="clip" data-start="{a:.3f}" data-duration="{b-a:.3f}" data-track-index="{track[0]}" style="position:absolute;left:{x}px;top:{y}px;width:{w}px;{hs}z-index:{z};{style}">{extra}</div>')
    def mk(t, cls="", id_=""):
        idattr = f' id="{id_}"' if id_ else ""
        return f'<span class="mk"><span class="rise {cls}"{idattr}>{t}</span></span>'
    def chars(t, pre):
        return "".join(f'<span class="ch" id="{pre}{i}">{html.escape(c) if c != " " else "&nbsp;"}</span>' for i, c in enumerate(t))
    def blink(sel, t0, t1, on_first=True):
        n = int(round((t1 - t0) / (B / 2)))
        for k in range(n):
            J(f'tl.set("{sel}",{{opacity:{1 if (k % 2 == 0) == on_first else 0}}},{t0 + k*B/2:.4f});')
    def ab(x, y, extra=""):
        return f'style="position:absolute;left:{x}px;top:{y}px;{extra}"'
    # ---- sizes
    S = dict(
        mono_cur=96 if L else 88, serif=84 if L else 76, a_use=360 if L else 300, a_read=300 if L else 290,
        a_cero=980 if L else 560, a_mid=210 if L else 230, a_big=300 if L else 330, a_burst=300 if L else 250,
        a_lime=320 if L else 360, mono_end=44 if L else 40, a_end=260 if L else 300, label=22 if L else 24)
    # ================= SHOT 1 (0 - 2.5) hook: cursor on stone
    cx, cy = (int(W*0.46), int(H*0.50) - 60) if L else (int(W*0.40), int(H*0.46) - 55)
    cur = lambda pid: f'<div class="mono cur" {ab(cx, cy)}><span id="{pid}gt" style="opacity:0">&gt;</span><span id="{pid}b">&#9608;</span></div>'
    sec(1, 0, 2.5, STONE, cur("c1"))
    blink("#c1b", 0, 2.5); J('tl.set("#c1gt",{opacity:1},1.25);')
    # ================= SHOT 2 (2.5 - 5.0) stone, tension 1
    anchor = lambda col: f'<div class="mono" {ab(G, H - (150 if L else 380), f"font-size:28px;color:{col}")}>&gt;&#9608;</div>'
    ty = int(H*0.30) if L else int(H*0.33)
    sec(2, 2.5, 5.0, STONE, anchor(MUTED) + f'<div {ab(G, ty)}><div class="serif" style="font-size:{S["serif"]}px">{mk(TXT["s2a"])}</div>'
        f'<div class="anton" id="s2u" style="font-size:{S["a_use"]}px;opacity:0">{TXT["s2b"]}</div></div>')
    J('tl.fromTo("#s2 .rise",{yPercent:110},{yPercent:0,duration:0.35,ease:"expo.out"},2.5);')
    J('tl.set("#s2u",{opacity:1},2.813);')
    # ================= SHOT 3 (5.0 - 8.125) ink, tension 2
    lines3 = TXT["s3b_d"] if L else TXT["s3b_m"]
    s3serif = TXT["s3a"]
    sec(3, 5.0, 8.125, INK, anchor(STONE) + f'<div {ab(G, int(H*0.22) if L else int(H*0.25))} id="s3blk"><div class="serif" style="font-size:{S["serif"]}px;color:{STONE};max-width:{W-2*G}px">{chars(s3serif, "s3c")}</div>'
        + "".join(f'<div class="anton s3l" style="font-size:{S["a_read"]}px;color:{STONE};opacity:0">{l}</div>' for l in lines3) + '</div>')
    for i in range(len(s3serif)):
        J(f'tl.fromTo("#s3c{i}",{{opacity:0}},{{opacity:1,duration:0.001}},{5.0 + i*0.6/len(s3serif):.3f});')
    J('tl.set("#s3 .s3l",{opacity:1},5.938);')
    J('tl.to("#s3blk",{yPercent:12,opacity:0,duration:0.3,ease:"power4.in"},7.8);')
    # ================= SHOT 4 (8.125 - 9.375) held breath
    sec(4, 8.125, 9.375, INK, f'<div class="mono cur" {ab(int(W/2-30), cy)} ><span id="c4b" style="color:{STONE}">&#9608;</span></div>')
    blink("#c4b", 8.125, 9.375)
    # ================= SHOT 5 (9.375 - 11.875) CERO mask over real dialog
    sec(5, 9.375, 11.875, INK, "")
    vw5 = W if L else 1500; vh5 = int(vw5 * 1116 / 1776); vx5 = (W - vw5)//2; vy5 = (H - vh5)//2
    clip("video", "v5", "assets/h5.mp4", 9.375, 11.875, vx5, vy5, vw5, vh5, z=5)
    clip("div", "m5", "", 9.375, 11.875, 0, 0, W, H, z=8,
         style=f"background:{INK};mix-blend-mode:darken;display:flex;align-items:center;justify-content:center;",
         extra=f'<div class="anton" id="m5t" style="font-size:{S["a_cero"]}px;color:#fff;line-height:.86;letter-spacing:-.01em">CERO</div>')
    clip("div", "o5", "", 9.375, 10.625, 0, 0, W, H, z=9, style="display:flex;align-items:center;justify-content:center;pointer-events:none",
         extra=f'<div class="anton" style="font-size:{S["a_cero"]}px;color:transparent;-webkit-text-stroke:2px {STONE};line-height:.86;letter-spacing:-.01em">CERO</div>')
    # box target of shot 6
    bx, by, bw = (823, 233, 977) if L else (64, 940, 952)
    bh = int(bw * 1116 / 1776)
    J(f'tl.fromTo("#v5",{{scale:1}},{{scale:1.06,duration:1.25,ease:"none",transformOrigin:"50% 50%"}},9.375);')
    J(f'tl.fromTo("#m5t",{{scale:1}},{{scale:14,duration:0.8,ease:"power3.in",transformOrigin:"31% 50%"}},10.625);')
    J('tl.to("#m5",{opacity:0,duration:0.15},11.3);')
    sx = bw / vw5
    J(f'tl.fromTo("#v5",{{x:0,y:0}},{{x:{bx - vx5 - (vw5 - vw5*1.06)/2:.1f},y:{by - vy5 - (vh5-vh5*1.06)/2:.1f},scale:{sx:.4f},transformOrigin:"0% 0%",duration:0.55,ease:"expo.inOut"}},11.32);')
    # ================= SHOT 6 (11.875 - 15.625) See the command BEFORE IT RUNS.
    cap6 = (f'<div {ab(G, 300 if L else 330)}><div class="serif" style="font-size:{S["serif"]}px">{mk(TXT["s6a"])}</div>'
            + "".join(f'<div class="anton s6l" style="font-size:{S["a_mid"]}px;opacity:0">{l}</div>' for l in (TXT["s6b_d"] if L else TXT["s6b_m"])) + '</div>')
    lab = lambda x, y, t="real app footage", col=MUTED: f'<div class="mono" {ab(x, y, f"font-size:{S["label"]}px;color:{col}")}>{t}</div>'
    brk = lambda x, y, w, h, col=INK: (f'<div class="brk" {ab(x-14, y-14, f"width:28px;height:28px;border-left:3px solid {col};border-top:3px solid {col}")}></div>'
                                      f'<div class="brk" {ab(x+w-14, y+h-14, f"width:28px;height:28px;border-right:3px solid {col};border-bottom:3px solid {col}")}></div>')
    sec(6, 11.875, 15.625, STONE, cap6 + brk(bx, by, bw, bh) + lab(bx, by + bh + 18))
    clip("video", "v6", "assets/h6.mp4", 11.875, 15.625, bx, by, bw, bh, z=5)
    # underline under "Nothing runs until you approve." (fractions of the hero crop)
    ux0, ux1, uy = 0.552, 0.908, 0.368
    if L:
        clip("div", "u6", "", 13.125, 15.625, int(bx + ux0*bw), int(by + uy*bh), int((ux1-ux0)*bw), 5, z=7, style=f"background:{LIME};transform-origin:0 50%")
        J('tl.fromTo("#u6",{scaleX:0},{scaleX:1,duration:0.5,ease:"expo.out"},13.125);')
    else:
        # 9:16 push-in: region x .03-.97, y .26-.46 fills the box width
        rx0, rx1, ry0 = 0.03, 0.97, 0.26
        s = 1 / (rx1 - rx0)
        J(f'tl.to("#v6",{{scale:{s:.3f},x:{-rx0*bw*s:.1f},y:{-ry0*bh*s + 120:.1f},transformOrigin:"0% 0%",duration:0.6,ease:"expo.inOut"}},12.9);')
        ux = bx + (ux0 - rx0)*bw*s; uw = (ux1-ux0)*bw*s; uyy = by + 120 + (uy - ry0)*bh*s
        clip("div", "u6", "", 13.125, 15.625, int(ux), int(uyy), int(uw), 6, z=7, style=f"background:{LIME};transform-origin:0 50%")
        J('tl.fromTo("#u6",{scaleX:0},{scaleX:1,duration:0.5,ease:"expo.out"},13.6);')
    J('tl.fromTo("#s6 .rise",{yPercent:110},{yPercent:0,duration:0.35,ease:"expo.out"},11.875);')
    J('tl.fromTo("#s6 .s6l",{opacity:0,x:-40},{opacity:1,x:0,duration:0.3,ease:"expo.out"},12.188);')
    # page fold 15.313-15.625 (stone page turning over from the right edge)
    clip("div", "f6", "", 15.25, 15.625, 0, 0, W, H, z=20, style="perspective:2600px;",
         extra=f'<div id="f6p" style="position:absolute;inset:0;background:{STONE};transform-origin:0% 50%;box-shadow:none"></div>')
    J('tl.fromTo("#f6p",{rotationY:-178},{rotationY:0,duration:0.36,ease:"power3.inOut"},15.265);')
    # ================= SHOT 7 (15.625 - 19.375) Questions JUST GET ANSWERED.
    qw = 1400 if L else 1848; qh = int(qw * 186 / 2520)
    qx, qy = (G, 650) if L else (64, 1080)
    cap7 = (f'<div {ab(G, 190 if L else 300)}><div class="serif" style="font-size:{S["serif"]}px">{mk(TXT["s7a"])}</div>'
            + "".join(f'<div class="anton s7l" style="font-size:{S["a_mid"]}px;opacity:0">{l}</div>' for l in (TXT["s7b_d"] if L else TXT["s7b_m"])) + '</div>')
    sec(7, 15.625, 19.375, STONE, cap7 + f'<div {ab(qx-24, qy-24, f"width:{(qw+48) if L else 2200}px;height:{qh+48}px;background:{PLATE}")} id="pl7"></div>'
        + f'<div class="mono" {ab(G, qy+qh+50, f"font-size:{S["label"]+6}px;color:{MUTED}")}>{TXT["s7c"]} &nbsp;·&nbsp; real app footage</div>')
    clip("video", "v7", "assets/q.mp4", 15.625, 19.375, qx, qy, qw, qh, z=5)
    # lime highlight behind "42.5 (3 rows)" : fraction of q strip (2nd line) x .713-.985, y .52-.98
    hx0, hx1 = 0.762, 0.995
    clip("div", "h7", "", 16.875, 19.375, int(qx + hx0*qw), int(qy + qh + 4), int((hx1-hx0)*qw), 5, z=6,
         style=f"background:{LIME};transform-origin:0 50%")
    J('tl.fromTo("#h7",{scaleX:0},{scaleX:1,duration:0.25,ease:"expo.out"},16.875);')
    if not L:
        dx = -(qw - (W - 2*G)) - 0
        J(f'tl.fromTo("#v7",{{x:0}},{{x:{dx},duration:2.875,ease:"sine.inOut"}},15.625);')
        J(f'tl.fromTo("#h7",{{x:0}},{{x:{dx},duration:2.875,ease:"sine.inOut",immediateRender:false}},15.625);')
        J(f'tl.fromTo("#pl7",{{x:0}},{{x:{dx},duration:2.875,ease:"sine.inOut"}},15.625);')
    J('tl.fromTo("#s7 .rise",{yPercent:110},{yPercent:0,duration:0.35,ease:"expo.out"},16.875);')
    J('tl.set("#s7 .s7l",{opacity:1},17.188);')
    # ================= SHOT 8 (19.375 - 23.125) It ASKS. It doesn't guess. (ink)
    aw = 898 if L else 952; ah = int(aw * 1336 / 2112)
    ax, ay = (W - G - aw, (H - ah)//2) if L else (64, 1000)
    cap8 = (f'<div {ab(G, 330 if L else 330)}><div class="serif" style="font-size:{S["serif"]}px;color:{STONE}">{mk(TXT["s8b"])}</div>'
            f'<div style="white-space:nowrap"><span class="serif" style="font-size:{S["serif"]}px;color:{STONE}">It </span>'
            f'<span class="anton" id="s8a" style="font-size:{S["a_mid"]}px;color:{STONE};opacity:0">{TXT["s8a"]}</span></div></div>')
    sec(8, 19.375, 23.125, INK, cap8 + brk(ax, ay, aw, ah, STONE) + lab(ax, ay + ah + 18, col="#8A8A85")
        + f'<img src="assets/ask.png" alt="Cero asks: did you mean gitbrains" {ab(ax, ay, f"width:{aw}px")}/>'
        + f'<div id="o8" {ab(int(ax + 0.408*aw), int(ay + 0.085*ah), f"width:{int(0.352*aw)}px;height:{int(0.08*ah)}px;border:4px solid {LIME};margin:-8px;padding:4px;box-sizing:content-box")}></div>')
    J('tl.set("#s8a",{opacity:1},19.688);')
    J('tl.fromTo("#o8",{clipPath:"inset(0 100% 0 0)"},{clipPath:"inset(0 0% 0 0)",duration:0.4,ease:"expo.out"},20.0);')
    J('tl.fromTo("#s8 .rise",{yPercent:110},{yPercent:0,duration:0.35,ease:"expo.out"},19.375);')
    # ================= SHOT 9 (23.125 - 26.875) burst, 6 beats
    stills = [("hero.png", 1776, 1116), ("flow.png", 1776, 1412), ("flow5.png", 1312, 1328), ("port.png", 1312, 960), (None, 0, 0), ("sleep.png", 1776, 1116)]
    words = TXT["burst_d"] if L else TXT["burst_m"]
    for k in range(6):
        a = 23.125 + k*B; bgc = STONE if k % 2 == 0 else INK; fg = INK if k % 2 == 0 else STONE
        img = ""
        if stills[k][0]:
            fn, iw, ih = stills[k]
            if L:
                hh = 588; ww = int(hh*iw/ih); ix = W - G - ww; iy = 412
            else:
                hh = 760; ww = int(hh*iw/ih)
                if ww > 952: ww = 952; hh = int(ww*ih/iw)
                ix = (W - ww)//2; iy = 900
            img = f'<img class="bimg" src="assets/{fn}" alt="real Cero approval dialog" {ab(ix, iy, f"width:{ww}px;transform-origin:50% 50%")}/>'
        wtxt = words[k]
        if False:
            t = f'<div class="serif" {ab(G, int(H*0.16) if L else 320, f"font-size:{S["serif"]+20}px;color:{fg}")}>{wtxt}</div>'
        else:
            fs = S["a_burst"]
            t = (f'<div {ab(G, 110 if L else 300, f"z-index:3")}><div class="anton bw" id="bw{k}s" style="font-size:{fs}px;color:{fg}">{wtxt}</div></div>'
                 f'<div {ab(G, 110 if L else 300, f"z-index:3")}><div class="anton bw" id="bw{k}o" style="font-size:{fs}px;color:transparent;-webkit-text-stroke:3px {fg};opacity:0">{wtxt}</div></div>')
        labt = 'real app footage' if k in (0, 1, 5) else 'real app screenshot'
        labhtml = (f'<div class="mono" {ab(ix, iy + hh + 16, f"font-size:{S["label"]}px;color:#8A8A85;z-index:4")}>{labt}</div>' if stills[k][0] else '')
        sec(f"9_{k}", a, a + B, bgc, img + t + labhtml)
        if k < 5:
            J(f'tl.set("#bw{k}s",{{opacity:0}},{a + E:.4f}); tl.set("#bw{k}o",{{opacity:1}},{a + E:.4f});')
        else:
            J(f'tl.set("#bw{k}s",{{opacity:0}},{a:.4f}); tl.set("#bw{k}o",{{opacity:1}},{a:.4f});')
        J(f'tl.fromTo("#s9_{k} .bimg",{{scale:1}},{{scale:1.02,duration:{B},ease:"none"}},{a:.4f});')
    # ================= SHOT 10 (26.875 - 28.75) ASKS FIRST.
    sec(10, 26.875, 28.75, STONE, f'<div class="anton" style="position:absolute;inset:0;display:flex;flex-direction:column;justify-content:center;padding-left:{G}px;font-size:{S["a_big"] + (40 if L else 60)}px;line-height:.9">'
        + "".join(f"<div>{l}</div>" for l in (TXT["s10_d"] if L else TXT["s10_m"])) + '</div>')
    # ================= SHOT 11 (28.75 - 32.5) NO means no.
    if L:
        b11 = (980, 140, 816); dlx, dly, dlw = 520, 640, 1300
    else:
        b11 = (64, 760, 952); dlx, dly, dlw = 64, 1000, 2360
    b11h = int(b11[2] * 1116 / 1776); dlh = int(dlw * 124 / 2360)
    sec(11, 28.75, 32.5, STONE, f'<div {ab(G, 260 if L else 300)}><span class="anton" style="font-size:{S["a_big"]}px">{TXT["s11a"]}</span>'
        f'{"" if L else "<br/>"}<span class="serif" id="s11b" style="font-size:{S["serif"]+10}px;opacity:0">&nbsp;{TXT["s11b"]}</span></div>'
        + lab(G, (b11[1] + b11h + 18) if not L else 1000, "real app footage"))
    clip("video", "v11", "assets/h11.mp4", 28.75, 30.0, b11[0], b11[1], b11[2], b11h, z=5)
    clip("div", "pl11", "", 30.0, 32.5, dlx - 24, dly - 20, (dlw + 48), dlh + 40, z=4, style=f"background:{PLATE}")
    clip("video", "v11d", "assets/decl.mp4", 30.0, 32.5, dlx, dly, dlw, dlh, z=5)
    # underline under "Declined in the confirmation dialog." fraction x .66-.99 on line 2
    clip("div", "u11", "", 30.625, 32.5, int(dlx + 0.598*dlw), int(dly + dlh*0.97), int(0.392*dlw), 5, z=7, style=f"background:{LIME};transform-origin:0 50%")
    J('tl.set("#s11b",{opacity:1},30.0);')
    J('tl.fromTo("#u11",{scaleX:0},{scaleX:1,duration:0.4,ease:"expo.out"},30.625);')
    if not L:
        dx = -(dlw - (W - 2*G))
        for el in ["#v11d", "#pl11", "#u11"]:
            J(f'tl.fromTo("{el}",{{x:0}},{{x:{dx},duration:2.0,ease:"sine.inOut",immediateRender:false}},30.0);')
    # ================= SHOT 12 (32.5 - 36.875) Ctrl+C means STOP.
    sw = 960; sh = int(sw * 208 / 1600)
    sx_, sy_ = (840, 420) if L else (60, 940)
    kx, ky = (G, 400) if L else (int(W/2 - 170), 560)
    sec(12, 32.5, 36.875, INK, f'<div id="key" class="mono" {ab(kx, ky, f"font-size:{72 if L else 80}px;color:{STONE};border:4px solid {STONE};border-bottom-width:12px;border-radius:14px;padding:14px 34px;opacity:0")}>Ctrl+C</div>'
        + f'<div {ab(G if L else G, (720 if L else 1260))}><span class="serif" id="s12a" style="font-size:{S["serif"]+10}px;color:{STONE};opacity:0">{TXT["s12a"]} </span>'
          f'<span class="anton" id="s12b" style="font-size:{S["a_big"]}px;color:{STONE};opacity:0">{TXT["s12b"]}</span></div>'
        + lab(sx_, sy_ + sh + 40, col="#8A8A85"))
    clip("div", "pl12", "", 32.5, 36.875, sx_ - 24, sy_ - 24, sw + 48, sh + 48, z=4, style=f"background:{PLATE}")
    clip("video", "v12", "assets/stop.mp4", 32.5, 36.875, sx_, sy_, sw, sh, z=5)
    J('tl.set("#key",{opacity:1},32.813);')
    J('tl.to("#key",{y:4,borderBottomWidth:"8px",duration:0.06},34.875); tl.to("#key",{y:0,borderBottomWidth:"12px",duration:0.08},35.05);')
    J('tl.set("#s12a",{opacity:1},35.0); tl.set("#s12b",{opacity:1},35.0);')
    # ================= SHOT 13 (36.875 - 37.5) breath, then lime page fold
    sec(13, 36.875, 37.5, INK, "")
    clip("div", "f13", "", 36.95, 37.5, 0, 0, W, H, z=20, style="perspective:2600px;",
         extra=f'<div id="f13p" style="position:absolute;inset:0;background:{LIME};transform-origin:0% 50%"></div>')
    J('tl.fromTo("#f13p",{rotationY:-178},{rotationY:0,duration:0.5,ease:"power3.inOut"},37.0);')
    # ================= SHOT 14 (37.5 - 41.25) lime: AI in your terminal. YOUR HAND ON ENTER.
    sec(14, 37.5, 41.25, LIME, f'<div id="s14blk" {ab(G, int(H*0.24) if L else int(H*0.27))}><div class="serif" style="font-size:{S["serif"]}px">{mk(TXT["s14a"])}</div>'
        + "".join(f'<div class="mk"><div class="rise anton s14l" style="font-size:{S["a_lime"]}px">{l}</div></div><br/>' for l in (TXT["s14b_d"] if L else TXT["s14b_m"])) + '</div>')
    J('tl.fromTo("#s14 .serif .rise",{yPercent:110},{yPercent:0,duration:0.4,ease:"expo.out"},37.5);')
    J('tl.fromTo("#s14 .s14l",{yPercent:110},{yPercent:0,duration:0.5,ease:"expo.out",stagger:0.08},38.125);')
    J('tl.fromTo("#s14blk",{y:0},{y:-14,duration:3.1,ease:"none"},38.125);')
    # ================= SHOT 15 (41.25 - 43.75) mono lines on lime
    m15 = TXT["s15_d"] if L else TXT["s15_m"]
    blk15 = "".join(f'<div class="mono" style="font-size:{S["mono_end"]}px;line-height:1.45">{chars(l, f"s15_{j}_")}</div>' for j, l in enumerate(m15))
    sec(15, 41.25, 43.75, LIME, f'<div {ab(G, int(H*0.40) if L else int(H*0.40))}>{blk15}</div>')
    starts = [41.25, 41.875, 42.2] if not L else [41.25, 41.875]
    for j, l in enumerate(m15):
        t0 = starts[min(j, len(starts)-1)]
        for i in range(len(l)):
            J(f'tl.fromTo("#s15_{j}_{i}",{{opacity:0}},{{opacity:1,duration:0.001}},{t0 + i*0.4/len(l):.3f});')
    # ================= SHOT 16 (43.75 - 46.875) end card
    ex, ey = (cx - 300, cy - 200) if L else (0, int(H*0.30))
    sec(16, 43.75, 46.875, STONE,
        f'<div {ab(ex, ey, "" if L else f"width:{W}px;display:flex;flex-direction:column;align-items:center")}><div style="white-space:nowrap"><span class="anton" style="font-size:{S["a_end"]}px">'
        + "".join(f'<span id="e{i}" style="display:none">{c}</span>' for i, c in enumerate("CERO"))
        + f'</span><span id="e_c" style="display:inline-block;width:{int(S["a_end"]*0.42)}px;height:{int(S["a_end"]*0.74)}px;background:{INK};margin-left:14px;vertical-align:baseline"></span></div>'
        f'<div id="e_by" style="display:flex;align-items:center;gap:22px;margin-top:22px;opacity:0"><span class="serif" style="font-size:{S["serif"]}px">by</span>'
        f'<img src="assets/logo.png" alt="Magnm logo" style="width:{240 if L else 360}px;mix-blend-mode:multiply"/></div>'
        f'<div class="mono" id="e_url" style="font-size:{S["mono_end"]+4}px;margin-top:26px;opacity:0">magnm-org.vercel.app</div></div>')
    J('tl.set("#e_c",{opacity:0},44.0); tl.set("#e_c",{opacity:1},44.3);')
    for i in range(4):
        J(f'tl.set("#e{i}",{{display:"inline"}},{44.375 + i*0.156:.3f});')
    blink("#e_c", 44.375 + 0.7, 46.875)
    J('tl.set("#e_by",{opacity:1},45.0); tl.set("#e_url",{opacity:1},45.625);')
    # grain overlay (shifts every 2 frames)
    clip("div", "grain", "", 0, TOTAL, 0, 0, W, H, z=40, style="background:url(assets/grain.png);pointer-events:none;mix-blend-mode:multiply;opacity:.55")
    for k in range(int(TOTAL*15)):
        J(f'tl.set("#grain",{{backgroundPosition:"{(k*137)%512}px {(k*251)%512}px"}},{k/15:.3f});')
    css = f'''
@font-face{{font-family:"Anton";src:url("assets/fonts/anton.woff2") format("woff2");}}
@font-face{{font-family:"Instrument Serif";font-style:italic;src:url("assets/fonts/serif-italic.woff2") format("woff2");}}
@font-face{{font-family:"JetBrains Mono";font-weight:500;src:url("assets/fonts/mono.woff2") format("woff2");}}
*{{margin:0;padding:0;box-sizing:border-box}}
html,body{{width:{W}px;height:{H}px;overflow:hidden;background:{STONE}}}
#root{{position:relative;width:{W}px;height:{H}px;overflow:hidden;color:{INK}}}
.clip{{position:absolute;left:0;top:0}}
.sec{{width:{W}px;height:{H}px;overflow:hidden;z-index:1}}
.anton{{font-family:"Anton",sans-serif;line-height:.9;letter-spacing:-.005em;white-space:nowrap}}
.serif{{font-family:"Instrument Serif",serif;font-style:italic;line-height:1.1}}
.mono{{font-family:"JetBrains Mono",monospace;font-weight:500}}
.cur{{font-size:{S["mono_cur"]}px;white-space:nowrap}}
.mk{{display:inline-block;overflow:hidden;vertical-align:top;padding-bottom:.06em}} .rise{{display:inline-block}}
'''
    html_ = f'''<!doctype html>
<html lang="en"><head><meta charset="UTF-8"/><meta name="viewport" content="width={W}, height={H}"/><title>Cero - See it before it runs ({fmt})</title>
<script src="assets/gsap.min.js"></script><style>{css}</style></head>
<body><div id="root" data-composition-id="main" data-start="0" data-duration="{TOTAL}" data-width="{W}" data-height="{H}">
{chr(10).join(secs)}
{chr(10).join(clips)}
<audio id="score" src="assets/score.wav" data-start="0" data-duration="{TOTAL}" data-track-index="90" data-volume="1"></audio>
</div>
<script>
window.__timelines = window.__timelines || {{}};
const tl = gsap.timeline({{ paused: true }});
{chr(10).join(js)}
window.__timelines["main"] = tl;
tl.seek(0);
</script></body></html>'''
    open(f"{P}/index.html", "w").write(html_)
    print(fmt, "ok", len(js), "tweens")

for f in (sys.argv[1:] or ["desktop", "mobile"]):
    build(f)
