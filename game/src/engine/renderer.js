import * as THREE from 'three';
import { EffectComposer } from 'three/examples/jsm/postprocessing/EffectComposer.js';
import { RenderPass } from 'three/examples/jsm/postprocessing/RenderPass.js';
import { UnrealBloomPass } from 'three/examples/jsm/postprocessing/UnrealBloomPass.js';
import { GTAOPass } from 'three/examples/jsm/postprocessing/GTAOPass.js';
import { OutputPass } from 'three/examples/jsm/postprocessing/OutputPass.js';
import { ShaderPass } from 'three/examples/jsm/postprocessing/ShaderPass.js';
import { SMAAPass } from 'three/examples/jsm/postprocessing/SMAAPass.js';
import { BokehPass } from 'three/examples/jsm/postprocessing/BokehPass.js';

/*
 * Cinematic final-grade pass:
 *  - filmic color grade (lift / gamma / gain + saturation + warm-shadow / cool-highlight split tone)
 *  - chromatic aberration (radial), scaled toward the edges
 *  - vignette (dynamic, boosted while scoped / on damage)
 *  - animated film grain
 */
const GradeShader = {
  uniforms: {
    tDiffuse: { value: null },
    uTime: { value: 0 },
    uVignette: { value: 0.9 },
    uAberration: { value: 0.0014 },
    uGrain: { value: 0.018 },
    uSat: { value: 1.09 },
    uContrast: { value: 1.07 },
    uLift: { value: new THREE.Vector3(0.01, 0.006, 0.0) },
    uGain: { value: new THREE.Vector3(1.02, 1.0, 0.97) },
  },
  vertexShader: /* glsl */`
    varying vec2 vUv;
    void main(){ vUv = uv; gl_Position = projectionMatrix * modelViewMatrix * vec4(position,1.0); }
  `,
  fragmentShader: /* glsl */`
    varying vec2 vUv;
    uniform sampler2D tDiffuse;
    uniform float uTime, uVignette, uAberration, uGrain, uSat, uContrast;
    uniform vec3 uLift, uGain;
    float hash(vec2 p){ p = fract(p*vec2(123.34,456.21)); p += dot(p, p+45.32); return fract(p.x*p.y); }
    void main(){
      vec2 uv = vUv;
      vec2 c = uv - 0.5;
      float r2 = dot(c,c);
      // chromatic aberration — offset R/B radially, strengthening to edges
      vec2 dir = c * (uAberration * (1.0 + r2*2.5));
      vec3 col;
      col.r = texture2D(tDiffuse, uv + dir).r;
      col.g = texture2D(tDiffuse, uv).g;
      col.b = texture2D(tDiffuse, uv - dir).b;

      // lift / gain color grade
      col = col * uGain + uLift;
      // contrast around 0.5 pivot
      col = (col - 0.5) * uContrast + 0.5;
      // saturation
      float l = dot(col, vec3(0.2126,0.7152,0.0722));
      col = mix(vec3(l), col, uSat);
      // gentle split-tone: warm shadows, cool highlights
      col += (0.5 - l) * vec3(0.02, 0.008, -0.02);

      // vignette
      float vig = smoothstep(0.9, 0.15, r2 * uVignette * 2.0);
      col *= mix(1.0, vig, 0.85);

      // film grain (animated)
      float g = hash(uv * vec2(1920.0,1080.0) + fract(uTime)*97.0);
      col += (g - 0.5) * uGrain;

      gl_FragColor = vec4(clamp(col,0.0,1.0), 1.0);
    }
  `,
};

export class Renderer {
  constructor(canvas, opts = {}) {
    this.renderer = new THREE.WebGLRenderer({
      canvas, antialias: false, powerPreference: 'high-performance', stencil: false,
      preserveDrawingBuffer: !!opts.preserveDrawingBuffer, // needed for toDataURL capture
    });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 1.75));
    this.renderer.outputColorSpace = THREE.SRGBColorSpace;
    this.renderer.toneMapping = THREE.ACESFilmicToneMapping;
    this.renderer.toneMappingExposure = 0.98;
    this.renderer.shadowMap.enabled = true;
    this.renderer.shadowMap.type = THREE.VSMShadowMap; // soft shadows (PCFSoft deprecated in r185)

    this.width = window.innerWidth;
    this.height = window.innerHeight;
  }

  build(scene, camera) {
    this.scene = scene; this.camera = camera;
    const r = this.renderer;
    r.setSize(this.width, this.height, false); // backing store; keep CSS sizing
    const size = new THREE.Vector2(this.width, this.height);
    const composer = new EffectComposer(r);
    composer.setPixelRatio(r.getPixelRatio());
    composer.setSize(this.width, this.height);

    const renderPass = new RenderPass(scene, camera);

    // Ambient occlusion (ground-truth GTAO)
    const gtao = new GTAOPass(scene, camera, this.width, this.height);
    gtao.output = GTAOPass.OUTPUT.Default;
    // tuned AO — subtle, contact-shadow feel
    gtao.updateGtaoMaterial({ radius: 0.35, distanceExponent: 1.2, thickness: 1.0, scale: 1.4, samples: 16 });
    gtao.updatePdMaterial({ lumaPhi: 10, depthPhi: 2, normalPhi: 3, radius: 4, radiusExponent: 1, rings: 2, samples: 16 });
    gtao.blendIntensity = 1.0;

    const bloom = new UnrealBloomPass(size, 0.48, 0.6, 0.9);
    bloom.threshold = 0.9; bloom.strength = 0.48; bloom.radius = 0.6;

    const bokeh = new BokehPass(scene, camera, { focus: 60.0, aperture: 0.00002, maxblur: 0.004 });

    const output = new OutputPass();

    const grade = new ShaderPass(GradeShader);
    grade.renderToScreen = false;

    const smaa = new SMAAPass(this.width, this.height);

    composer.addPass(renderPass);
    composer.addPass(gtao);
    composer.addPass(bloom);
    composer.addPass(bokeh);
    composer.addPass(output);   // ACES tonemap + sRGB
    composer.addPass(grade);    // filmic grade (LDR)
    composer.addPass(smaa);     // AA last

    this.composer = composer;
    this.passes = { renderPass, gtao, bloom, bokeh, output, grade, smaa };
    return this;
  }

  setScoped(scoped, focusDist = 60) {
    const b = this.passes.bokeh.uniforms;
    const g = this.passes.grade.uniforms;
    this._scoped = scoped;
    if (scoped) {
      b['focus'].value = focusDist;
      b['aperture'].value = 0.00022;   // shallow DOF through glass
      b['maxblur'].value = 0.011;
      g.uVignette.value = 1.35;
      g.uAberration.value = 0.0024;
      g.uSat.value = 1.15;
      this.renderer.toneMappingExposure = 1.06;
    } else {
      b['focus'].value = focusDist;
      b['aperture'].value = 0.00002;   // near-deep focus at the hip
      b['maxblur'].value = 0.0035;
      g.uVignette.value = 0.9;
      g.uAberration.value = 0.0014;
      g.uSat.value = 1.09;
      this.renderer.toneMappingExposure = 0.98;
    }
  }

  // keep the plane of focus on whatever the player is aiming at
  setFocus(dist) {
    if (Number.isFinite(dist)) this.passes.bokeh.uniforms['focus'].value = dist;
  }

  update(dt) {
    this.passes.grade.uniforms.uTime.value += dt;
  }

  render() { this.composer.render(); }

  resize() {
    this.width = window.innerWidth; this.height = window.innerHeight;
    this.renderer.setSize(this.width, this.height);
    this.composer.setSize(this.width, this.height);
    this.camera.aspect = this.width / this.height;
    this.camera.updateProjectionMatrix();
  }
}
