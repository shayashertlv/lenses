/** The see-through room and the reflection limit (eyewear-reflection.ts): what canonical lenses and crystal reflect, and
 *  how that reflection is added to the camera seen through them. Tom Ford FT1123-D review, 2026-09-30: RoomEnvironment's
 *  +Z panel, behind the selfie camera, clipped to a flat white rectangle on every delivered lens (r0009: 10.2 % of the
 *  visible lens) and to the white slab on the crystal bridge (12 % of the crystal). */
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {Mesh, MeshLambertMaterial} from 'three';
import {RoomEnvironment} from 'three/addons/environments/RoomEnvironment.js';
import {
  createSeeThroughRoom, CRYSTAL_REFLECTION_KNEE, findFrontPanel, FRONT_PANEL, LENS_REFLECTION_KNEE, limitReflection, REFLECTION_LIMIT_GLSL,
  REFLECTION_WHITE,
} from '../src/render/eyewear-reflection.ts';
import type {ReflectionKnee} from '../src/render/eyewear-reflection.ts';

type Rgb = [number, number, number];
/** The room's emitters as [x, y, z, emissive] rows (area lights: the Lambert materials with an emissive intensity). */
function emitters(room: RoomEnvironment): number[][] {
  return room.children.filter((child): child is Mesh => child instanceof Mesh && child.material instanceof MeshLambertMaterial)
    .map(child => [...child.position.toArray(), (child.material as MeshLambertMaterial).emissiveIntensity]);
}
const srgb = (linear: number): number => 255 * (linear <= .0031308 ? 12.92 * linear : 1.055 * linear ** (1 / 2.4) - .055);

test('the front panel is three 0.185.1\'s +Z light4, the only emitter near a selfie camera\'s axis, 10-29 degrees above it', () => {
  const room = new RoomEnvironment();
  try {
    const panel = findFrontPanel(room), material = panel.material as MeshLambertMaterial;
    assert.equal(material.emissiveIntensity, FRONT_PANEL.emissiveIntensity);
    assert.deepEqual(panel.position.toArray(), [...FRONT_PANEL.position]);
    assert.deepEqual(panel.scale.toArray(), [...FRONT_PANEL.scale]);
    // The room is lowered by 3.5: seen from the eyewear at the origin the panel spans these elevations, the band a
    // camera-facing lens (view rays climbing 10-15 degrees in the harness) mirrors.
    const y = room.position.y + panel.position.y, z = panel.position.z, half = panel.scale.y / 2;
    const low = Math.atan2(y - half, z) * 180 / Math.PI, high = Math.atan2(y + half, z) * 180 / Math.PI;
    assert.ok(low > 9 && low < 11 && high > 28 && high < 30, `${low.toFixed(1)}..${high.toFixed(1)} degrees`);
    // The reason it clipped: F0 0.04 x scene intensity 0.8 x 43 on a material drawn without tone mapping.
    assert.ok(.04 * .8 * FRONT_PANEL.emissiveIntensity > 1);
    // The only emitter within 45 degrees of the camera's axis (light1, also at +z, is 67 degrees off to the side).
    const offAxis = emitters(room).filter(([, , , emissive]) => emissive! > 1).map(([px, py, pz]) =>
      Math.acos(pz! / Math.hypot(px!, py! + room.position.y, pz!)) * 180 / Math.PI);
    assert.deepEqual(offAxis.filter(angle => angle < 45).length, 1, offAxis.map(angle => angle.toFixed(0)).join(' '));
  } finally {room.dispose();}
});

test('the see-through room is the scene\'s room without the front panel, everything else identical', () => {
  const scene = new RoomEnvironment(), seeThrough = createSeeThroughRoom();
  try {
    const all = emitters(scene), kept = emitters(seeThrough);
    const panel = [...FRONT_PANEL.position, FRONT_PANEL.emissiveIntensity];
    assert.deepEqual(kept, all.filter(row => row.join() !== panel.join()), 'the other five emitters, same places and intensities');
    assert.equal(kept.length, all.length - 1);
    assert.equal(seeThrough.children.length, scene.children.length - 1, 'the point light, the room and its boxes stay');
    assert.throws(() => findFrontPanel(seeThrough), /exactly one front panel.*found 0/);
    assert.equal(seeThrough.position.y, scene.position.y);
  } finally {scene.dispose(); seeThrough.dispose();}
});

test('the removed panel\'s material is freed; the shared box geometry stays with the room', () => {
  const disposed: string[] = [];
  const original = MeshLambertMaterial.prototype.dispose;
  MeshLambertMaterial.prototype.dispose = function(this: MeshLambertMaterial) {disposed.push(String(this.emissiveIntensity)); original.call(this);};
  try {
    const room = createSeeThroughRoom();
    assert.deepEqual(disposed, ['43']);
    const geometry = (room.children.find(child => child instanceof Mesh) as Mesh).geometry;
    assert.ok(geometry.getAttribute('position'), 'the room still draws with its box');
    room.dispose();
  } finally {MeshLambertMaterial.prototype.dispose = original;}
});

test('a three upgrade that moves, dims or duplicates the panel fails loudly instead of silently changing the rooms', () => {
  const moved = new RoomEnvironment(), dimmed = new RoomEnvironment(), doubled = new RoomEnvironment();
  try {
    findFrontPanel(moved).position.x += .5;
    assert.throws(() => findFrontPanel(moved), /moved or changed intensity/);
    (findFrontPanel(dimmed).material as MeshLambertMaterial).emissiveIntensity = 40;
    assert.throws(() => findFrontPanel(dimmed), /moved or changed intensity/);
    doubled.add(findFrontPanel(doubled).clone());
    assert.throws(() => findFrontPanel(doubled), /found 2/);
  } finally {moved.dispose(); dimmed.dispose(); doubled.dispose();}
});

const KNEES: [string, ReflectionKnee][] = [['lens', LENS_REFLECTION_KNEE], ['crystal', CRYSTAL_REFLECTION_KNEE]];

test('the reflection limit leaves ordinary reflections exact and never lets a reflection reach the limit', () => {
  for (const [surface, knee] of KNEES) {
    // Below the knee nothing changes: a wall reflection on a brown lens, r0009's crystal median.
    for (const [reflected, through] of [[[.016, .016, .016], [.3, .22, .15]], [[.025, .025, .024], [.45, .43, .41]]] as [Rgb, Rgb][]) {
      assert.deepEqual(limitReflection(reflected, through, knee), reflected, surface);
    }
    // The panel on r0009's lens: 1.38 linear on a ~0.3 look-through went to 1.68 and clipped; now it stays below the limit.
    const panel = limitReflection([1.38, 1.38, 1.38], [.3, .22, .15], knee);
    assert.ok(.3 + panel[0] < REFLECTION_WHITE && .3 + panel[0] > .85, `${surface}: ${.3 + panel[0]}`);
    // Never above the limit, whatever the reflection (it is approached exponentially; a reflection many times over it
    // can round onto it), and the limit is below the observation's clipped threshold (min channel 250).
    assert.ok(srgb(REFLECTION_WHITE) < 250, `${srgb(REFLECTION_WHITE)}`);
    for (const through of [0, .1, .3, .6, .85, .899]) {
      let previous = -1;
      for (let r = 0; r <= 60; r += .05) {
        const [out] = limitReflection([r, r, r], [through, through, through], knee);
        assert.ok(through + out <= REFLECTION_WHITE + 1e-12, `${surface}: through ${through} reflection ${r}`);
        if (through <= .6 && r <= 3) assert.ok(through + out < REFLECTION_WHITE, `${surface}: strictly below it up to 3 linear: through ${through} reflection ${r}`);
        assert.ok(out >= previous - 1e-12, `${surface}: monotone: a brighter reflection is never drawn darker`);
        previous = out;
      }
    }
    // The look-through at or past the limit (a white wall through clear crystal): nothing is added.
    assert.deepEqual(limitReflection([.5, .4, .3], [.95, .95, .95], knee), [0, 0, 0], surface);
    assert.deepEqual(limitReflection([.5, .4, .3], [.9, .2, .2], knee), [0, 0, 0], `${surface}: the brightest look-through channel sets the headroom`);
  }
});

test('the shoulder is C1 at the knee and keeps the reflection\'s hue', () => {
  const through: Rgb = [.3, .25, .2], headroom = REFLECTION_WHITE - .3, h = 1e-6;
  for (const [surface, knee] of [['lens', .8 - .3], ['crystal', .5 * headroom]] as [string, number][]) {
    const at = (peak: number) => limitReflection([peak, peak, peak], through, surface === 'lens' ? LENS_REFLECTION_KNEE : CRYSTAL_REFLECTION_KNEE)[0];
    const below = (at(knee) - at(knee - h)) / h, above = (at(knee + h) - at(knee)) / h;
    assert.ok(Math.abs(below - 1) < 1e-6 && Math.abs(above - 1) < 1e-3, `${surface}: ${below} ${above}`);
    assert.ok(at(knee + .01) < knee + .01, `${surface}: the shoulder starts at ${knee}`);
  }
  for (const [surface, knee] of KNEES) {
    const tinted: Rgb = [1.2, .8, .4], out = limitReflection(tinted, through, knee);
    assert.ok(Math.abs(out[1] / out[0] - 2 / 3) < 1e-12 && Math.abs(out[2] / out[0] - 1 / 3) < 1e-12, `${surface}: scaled on its peak channel`);
    assert.ok(out[0] < tinted[0]);
  }
});

test('the GLSL is the same function with the same constants', () => {
  assert.match(REFLECTION_LIMIT_GLSL,
    /vec3 eyewearReflectionLimit\( const in vec3 reflected, const in vec3 through, const in float kneeShare, const in float kneeLevel \)/);
  assert.ok(REFLECTION_LIMIT_GLSL.includes('float brightest = max( max( through.r, through.g ), through.b );'));
  assert.ok(REFLECTION_LIMIT_GLSL.includes(`float headroom = max( ${REFLECTION_WHITE.toFixed(4)} - brightest, 0.0 );`));
  assert.ok(REFLECTION_LIMIT_GLSL.includes('float knee = min( max( kneeShare * headroom, kneeLevel - brightest ), headroom ), span = headroom - knee;'));
  assert.ok(REFLECTION_LIMIT_GLSL.includes('if ( peak <= knee ) return reflected;'));
  assert.ok(REFLECTION_LIMIT_GLSL.includes('return reflected * ( ( knee + span * ( 1.0 - exp( - ( peak - knee ) / span ) ) ) / peak );'));
});
