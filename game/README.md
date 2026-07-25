# OPERATION LONGSHOT — WebGL Sniper FPS

A Silent-Scope-style first-person sniper built in Three.js, targeting the
highest visual fidelity achievable in WebGL: golden-hour atmospheric lighting,
PBR materials with procedurally generated textures, soft (VSM) shadows,
ground-truth ambient occlusion (GTAO), bloom, depth-of-field, SMAA, and a
filmic color-grade pass (ACES tone mapping + chromatic aberration + vignette +
film grain).

## Run
```
npm install
npm run dev      # http://localhost:5173
```

## Controls
WASD move · Mouse look · RMB scope · LMB fire · Shift hold-breath/sprint · R reload · Space jump · Esc pause

## Architecture
- `src/engine/` — renderer + post FX, sky/sun/IBL
- `src/world/`  — procedural PBR textures, level/environment art
- `src/player/` — pointer-lock controller, collision, stamina
- `src/weapon/` — sniper viewmodel, recoil, muzzle flash, ADS
- `src/enemy/`  — soldier models, AI, hit detection, death
- `src/fx/`     — tracers, impacts, blood, decals, ambient dust
- `src/audio/`  — fully synthesized WebAudio SFX
- `src/ui/`     — HUD + scope overlay
- `tools/`      — headless render + visual-critic capture harness
