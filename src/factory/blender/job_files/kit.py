"""Coil & Cog Blender kit: what every machine model needs (D-123). Runs only inside Blender.

Shapes built from measurements, materials, the studio, camera moves, labels, the render loop
(frames already rendered are skipped, so a stopped render continues) and the MP4 step. A machine
model (e.g. alternator.py) builds its parts and animation with these. Copied into every render
job by the factory; ported from the 2026-11-06 trial (data/trials/blender_alternator/).
"""

import contextlib
import math
import os
import time
from pathlib import Path

import bmesh
import bpy
from mathutils import Matrix

# Brand colours (config/channels/coil-and-cog.yaml), in linear light.
BG = (0.0047, 0.0056, 0.0080)  # #0E1116, a little darker
ELECTRIC = (0.0, 0.63, 1.0)  # #00D1FF
WHITE = (0.92, 0.93, 0.95)

FPS = 30
HERE = Path(".")


def setup(fps: int, here: Path) -> None:
    """Called once by render.py: the job's frame rate and folder (for the font)."""
    global FPS, HERE
    FPS, HERE = fps, here


def frame_at(t: float) -> int:
    return int(round(t * FPS)) + 1


def at(a: float, b: float, f: float) -> float:
    """A moment a fraction f of the way from a to b (scene timing scales with the narration)."""
    return a + (b - a) * f


# --- objects and shapes ------------------------------------------------------------------------


def clear_scene() -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)


def link(obj, parent=None):
    if obj.name not in bpy.context.scene.collection.objects:
        bpy.context.scene.collection.objects.link(obj)
    if parent is not None:
        obj.parent = parent
    return obj


def empty(name: str, parent=None, location=(0, 0, 0)):
    obj = bpy.data.objects.new(name, None)
    obj.location = location
    return link(obj, parent)


def mesh_object(name: str, bm, material, parent=None, smooth=True):
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    if smooth:
        me.shade_smooth()
        with contextlib.suppress(AttributeError):  # older Blender: no sharp-by-angle
            me.set_sharp_from_angle(angle=math.radians(35))
    me.materials.append(material)
    return link(bpy.data.objects.new(name, me), parent)


def translate(x, y, z):
    return Matrix.Translation((x, y, z))


def rot_z(a):
    return Matrix.Rotation(a, 4, "Z")


def rot_x90():
    return Matrix.Rotation(math.pi / 2, 4, "X")


def cylinder(r: float, z0: float, z1: float, segments: int = 64):
    bm = bmesh.new()
    bmesh.ops.create_cone(bm, cap_ends=True, segments=segments, radius1=r, radius2=r,
                          depth=z1 - z0, matrix=translate(0, 0, (z0 + z1) / 2))  # fmt: skip
    return bm


def prism(r: float, z0: float, z1: float, sides: int):
    """A nut or other n-sided prism."""
    bm = bmesh.new()
    bmesh.ops.create_cone(bm, cap_ends=True, segments=sides, radius1=r, radius2=r,
                          depth=z1 - z0, matrix=translate(0, 0, (z0 + z1) / 2))  # fmt: skip
    return bm


def tube(r_in: float, r_out: float, z0: float, z1: float, segments: int = 96):
    """A ring (hollow cylinder) built directly, without booleans."""
    bm = bmesh.new()
    rings = []
    for r, z in ((r_out, z0), (r_out, z1), (r_in, z1), (r_in, z0)):
        rings.append([bm.verts.new((r * math.cos(2 * math.pi * i / segments),
                                    r * math.sin(2 * math.pi * i / segments), z))
                      for i in range(segments)])  # fmt: skip
    for k in range(4):
        a_ring, b_ring = rings[k], rings[(k + 1) % 4]
        for i in range(segments):
            j = (i + 1) % segments
            bm.faces.new((a_ring[i], a_ring[j], b_ring[j], b_ring[i]))
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)  # outward, or booleans cut the wrong side
    return bm


def box(size, center, angle: float = 0.0):
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=size, verts=bm.verts)
    bmesh.ops.rotate(bm, verts=bm.verts, cent=(0, 0, 0), matrix=rot_z(angle))
    bmesh.ops.translate(bm, vec=center, verts=bm.verts)
    return bm


def torus(major: float, minor: float, seg_major: int = 32, seg_minor: int = 12):
    bm = bmesh.new()
    rings = []
    for i in range(seg_major):
        a = 2 * math.pi * i / seg_major
        ring = []
        for j in range(seg_minor):
            b = 2 * math.pi * j / seg_minor
            d = major + minor * math.cos(b)
            ring.append(bm.verts.new((d * math.cos(a), d * math.sin(a), minor * math.sin(b))))
        rings.append(ring)
    for i in range(seg_major):
        for j in range(seg_minor):
            i2, j2 = (i + 1) % seg_major, (j + 1) % seg_minor
            bm.faces.new((rings[i][j], rings[i2][j], rings[i2][j2], rings[i][j2]))
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    return bm


def sphere(r: float):
    bm = bmesh.new()
    bmesh.ops.create_uvsphere(bm, u_segments=16, v_segments=8, radius=r)
    return bm


def merge(*parts):
    """Join several bmeshes into one (each part stays a separate shell)."""
    me = bpy.data.meshes.new("tmp")
    out = bmesh.new()
    for part in parts:
        part.to_mesh(me)
        part.free()
        out.from_mesh(me)
    bpy.data.meshes.remove(me)
    return out


def subtract(obj, cutters) -> None:
    """Boolean difference with a temporary cutter mesh, applied at once (static geometry)."""
    cutter = mesh_object("cutter", cutters, obj.data.materials[0], smooth=False)
    mod = obj.modifiers.new("cut", "BOOLEAN")
    mod.operation = "DIFFERENCE"
    mod.solver = "EXACT"
    mod.use_self = True  # a part can be several touching pieces (wall, face, ears)
    mod.use_hole_tolerant = True
    mod.object = cutter
    with bpy.context.temp_override(object=obj, active_object=obj, selected_objects=[obj]):
        bpy.ops.object.modifier_apply(modifier=mod.name)
    bpy.data.objects.remove(cutter, do_unlink=True)


def bevel(obj, width: float = 0.008) -> None:
    mod = obj.modifiers.new("bevel", "BEVEL")
    mod.width = width
    mod.segments = 2
    mod.limit_method = "ANGLE"
    mod.harden_normals = False


def poly_curve(name: str, points, depth: float, material, parent=None):
    """A round cable along the given points."""
    curve = bpy.data.curves.new(name, "CURVE")
    curve.dimensions = "3D"
    curve.bevel_depth = depth
    curve.bevel_resolution = 4
    spline = curve.splines.new("POLY")
    spline.points.add(len(points) - 1)
    for p, co in zip(spline.points, points, strict=True):
        p.co = (*co, 1)
    curve.materials.append(material)
    return link(bpy.data.objects.new(name, curve), parent)


# --- animation ---------------------------------------------------------------------------------


def fcurves(action):
    """Blender 4.4+ keeps F-curves in layered actions; older versions in action.fcurves."""
    if hasattr(action, "fcurves") and len(action.fcurves):
        return list(action.fcurves)
    curves = []
    for layer in getattr(action, "layers", []):
        for strip in layer.strips:
            for bag in getattr(strip, "channelbags", []):
                curves.extend(bag.fcurves)
    return curves


def key(obj, path: str, frame: int, value, interpolation: str = "BEZIER", index: int = -1):
    """A keyframe; BEZIER eases in and out, so every move starts and stops smoothly."""
    if index >= 0:
        getattr(obj, path)[index] = value
    else:
        setattr(obj, path, value)
    obj.keyframe_insert(data_path=path, frame=frame, index=index)
    anim = obj.animation_data
    if anim and anim.action:
        for fc in fcurves(anim.action):
            if fc.data_path == path:
                fc.keyframe_points[-1].interpolation = interpolation


def move(obj, frame0: int, frame1: int, start, end) -> None:
    key(obj, "location", frame0, start)
    key(obj, "location", frame1, end)


def node_value(material, node: str, socket: str, frame: int, value: float, interp="LINEAR"):
    sock = material.node_tree.nodes[node].inputs[socket]
    sock.default_value = value
    sock.keyframe_insert("default_value", frame=frame)
    for fc in fcurves(material.node_tree.animation_data.action):
        if fc.data_path == sock.path_from_id("default_value"):
            fc.keyframe_points[-1].interpolation = interp


def glow(material, frame: int, value: float) -> None:
    """Emission strength of a Principled material at a frame."""
    node_value(material, "Principled BSDF", "Emission Strength", frame, value)


def set_visible(obj, frame: int, visible: bool) -> None:
    obj.hide_render = not visible
    obj.keyframe_insert("hide_render", frame=frame)


def ramp(t, t0, t1, v0, v1):
    """Smooth step from v0 (before t0) to v1 (after t1)."""
    if t <= t0:
        return v0
    if t >= t1:
        return v1
    x = (t - t0) / (t1 - t0)
    return v0 + (v1 - v0) * (x * x * (3 - 2 * x))


def along(path, u):
    """The point a fraction u along a polyline."""
    lengths = [0.0]
    for a, b in zip(path, path[1:], strict=False):
        lengths.append(lengths[-1] + math.dist(a, b))
    target = u * lengths[-1]
    for i in range(1, len(lengths)):
        if lengths[i] >= target:
            seg = (target - lengths[i - 1]) / max(lengths[i] - lengths[i - 1], 1e-9)
            a, b = path[i - 1], path[i]
            return tuple(a[j] + (b[j] - a[j]) * seg for j in range(3))
    return path[-1]


# --- materials ---------------------------------------------------------------------------------


def principled(name, color, metallic=0.0, roughness=0.5, coat=0.0, emission=None, bump=None):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nodes, links = mat.node_tree.nodes, mat.node_tree.links
    bsdf = nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (*color, 1)
    bsdf.inputs["Metallic"].default_value = metallic
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Coat Weight"].default_value = coat
    if emission:
        bsdf.inputs["Emission Color"].default_value = (*emission, 1)
        bsdf.inputs["Emission Strength"].default_value = 0.0
    if bump:
        kind, scale, strength = bump
        tex_coord = nodes.new("ShaderNodeTexCoord")
        if kind == "noise":  # cast metal
            tex = nodes.new("ShaderNodeTexNoise")
            tex.inputs["Scale"].default_value = scale
            tex.inputs["Detail"].default_value = 8
        else:  # "lines": stacked laminations / wire turns along Z
            tex = nodes.new("ShaderNodeTexWave")
            tex.wave_type = "BANDS"
            tex.bands_direction = "Z"
            tex.inputs["Scale"].default_value = scale
        links.new(tex_coord.outputs["Object"], tex.inputs["Vector"])
        bmp = nodes.new("ShaderNodeBump")
        bmp.inputs["Strength"].default_value = strength
        bmp.inputs["Distance"].default_value = 0.02
        links.new(tex.outputs["Fac"], bmp.inputs["Height"])
        links.new(bmp.outputs["Normal"], bsdf.inputs["Normal"])
    return mat


def emission_only(name, color, strength):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    nodes.remove(nodes["Principled BSDF"])
    em = nodes.new("ShaderNodeEmission")
    em.name = "Glow"
    em.inputs["Color"].default_value = (*color, 1)
    em.inputs["Strength"].default_value = strength
    mat.node_tree.links.new(em.outputs["Emission"], nodes["Material Output"].inputs["Surface"])
    return mat


def base_materials():
    """The channel's common materials; a model adds its own."""
    return {
        "aluminium": principled("Cast aluminium", (0.60, 0.61, 0.63), 1.0, 0.42,
                                bump=("noise", 60.0, 0.15)),
        "steel": principled("Machined steel", (0.62, 0.63, 0.65), 1.0, 0.22),
        "dark_steel": principled("Laminated steel", (0.16, 0.17, 0.19), 1.0, 0.42,
                                 bump=("lines", 55.0, 0.35)),
        "copper": principled("Copper", (0.96, 0.52, 0.30), 1.0, 0.24,
                             bump=("lines", 140.0, 0.25)),
        "plastic": principled("Black plastic", (0.015, 0.015, 0.017), 0.0, 0.45, coat=0.3),
        "rubber": principled("Rubber", (0.02, 0.02, 0.022), 0.0, 0.55),
        "label": emission_only("Label", WHITE, 3.0),
        "current": emission_only("Current", ELECTRIC, 25.0),
    }  # fmt: skip


# --- labels ------------------------------------------------------------------------------------


def label(text: str, parent, location, camera, material, size=0.13):
    """A white label that always faces the camera; hidden until show_label."""
    curve = bpy.data.curves.new(f"Label {text}", "FONT")
    curve.body = text
    curve.align_x = "CENTER"
    curve.size = size
    curve.space_character = 1.15
    font_file = HERE / "Inter-SemiBold.ttf"
    if font_file.is_file():
        curve.font = bpy.data.fonts.load(str(font_file), check_existing=True)
    curve.materials.append(material)
    obj = link(bpy.data.objects.new(f"Label {text}", curve), parent)
    obj.location = location
    obj.constraints.new("COPY_ROTATION").target = camera
    obj.scale = (0, 0, 0)
    return obj


def toward(az_deg: float, dist: float, z: float):
    """A point between the part and the camera (at azimuth az_deg then), so a label floats in front
    of its part instead of inside it; the 9:16 frame has no room at the sides."""
    a = math.radians(az_deg)
    return (dist * math.cos(a), dist * math.sin(a), z)


def show_label(obj, t_in: float, t_out: float) -> None:
    for t, s in ((t_in, 0.0), (t_in + 0.35, 1.0), (t_out - 0.35, 1.0), (t_out, 0.0)):
        key(obj, "scale", frame_at(t), (s, s, s))


# --- studio and camera -------------------------------------------------------------------------


def studio(scene) -> None:
    """Dark charcoal background for the camera; soft studio strips for the metal to reflect;
    a warm key light, a cyan rim light, a fill and a light from below."""
    world = bpy.data.worlds.new("Studio")
    scene.world = world
    world.use_nodes = True
    nodes, links = world.node_tree.nodes, world.node_tree.links
    bg = nodes["Background"]
    sep = nodes.new("ShaderNodeSeparateXYZ")
    links.new(nodes.new("ShaderNodeTexCoord").outputs["Generated"], sep.inputs["Vector"])
    top = nodes.new("ShaderNodeMapRange")
    top.inputs["From Min"].default_value = 0.35
    top.inputs["From Max"].default_value = 0.85
    top.inputs["To Max"].default_value = 2.2
    links.new(sep.outputs["Z"], top.inputs["Value"])

    def math_node(op, a, b=None):
        n = nodes.new("ShaderNodeMath")
        n.operation = op
        for i, v in enumerate((a, b)):
            if v is None:
                continue
            if isinstance(v, float):
                n.inputs[i].default_value = v
            else:
                links.new(v, n.inputs[i])
        return n.outputs[0]

    side = math_node("MULTIPLY", math_node("POWER", math_node("ABSOLUTE", sep.outputs["X"]), 12.0),
                     3.0)  # fmt: skip
    strips = math_node("ADD", math_node("ADD", top.outputs["Result"], side), 0.06)
    camera_ray = nodes.new("ShaderNodeLightPath").outputs["Is Camera Ray"]
    # ShaderNodeMix sockets: 0 factor, 2/3 float A/B, 6/7 colour A/B; outputs 0 float, 2 colour
    strength = nodes.new("ShaderNodeMix")
    strength.data_type = "FLOAT"
    links.new(camera_ray, strength.inputs[0])
    links.new(strips, strength.inputs[2])
    strength.inputs[3].default_value = 1.0
    links.new(strength.outputs[0], bg.inputs["Strength"])
    colour = nodes.new("ShaderNodeMix")
    colour.data_type = "RGBA"
    links.new(camera_ray, colour.inputs[0])
    colour.inputs[6].default_value = (0.85, 0.88, 0.92, 1)
    colour.inputs[7].default_value = (*BG, 1)
    links.new(colour.outputs[2], bg.inputs["Color"])

    for name, loc, rot, size, energy, color in (
        ("Key", (-3.5, -3.5, 4.0), (50, 0, -45), (4, 2), 650, (1.0, 0.97, 0.93)),
        ("Rim cyan", (3.5, 3.0, 2.0), (-70, 0, 130), (1.0, 4.0), 650, ELECTRIC),
        ("Fill", (4.0, -3.0, -1.5), (110, 0, 50), (3, 3), 140, (0.85, 0.9, 1.0)),
        ("Under", (0, 0, -6.0), (180, 0, 0), (3, 3), 110, (0.9, 0.95, 1.0)),
    ):
        data = bpy.data.lights.new(name, "AREA")
        data.shape = "RECTANGLE"
        data.size, data.size_y = size
        data.energy = energy
        data.color = color
        obj = link(bpy.data.objects.new(name, data))
        obj.location = loc
        obj.rotation_euler = tuple(math.radians(r) for r in rot)


def orbit(target, dist, az_deg, el_deg):
    az, el = math.radians(az_deg), math.radians(el_deg)
    return (
        target[0] + dist * math.cos(el) * math.cos(az),
        target[1] + dist * math.cos(el) * math.sin(az),
        target[2] + dist * math.sin(el),
    )


def camera_rig(scene, keys):
    """keys: (time s, target, distance, azimuth °, elevation °); the camera eases between them."""
    times = [k[0] for k in keys]
    if any(b <= a for a, b in zip(times, times[1:], strict=False)):
        raise ValueError(f"camera keys out of order: {[round(t, 2) for t in times]}")
    target = empty("Camera target")
    data = bpy.data.cameras.new("Camera")
    data.lens = 50
    data.sensor_fit = "AUTO"
    data.clip_start = 0.05
    cam = link(bpy.data.objects.new("Camera", data))
    track = cam.constraints.new("TRACK_TO")
    track.target = target
    track.track_axis = "TRACK_NEGATIVE_Z"
    track.up_axis = "UP_Y"
    scene.camera = cam
    for t, tgt, dist, az, el in keys:
        key(target, "location", frame_at(t), tgt)
        key(cam, "location", frame_at(t), orbit(tgt, dist, az, el))
    return cam


# --- rendering ---------------------------------------------------------------------------------


def setup_render(scene, engine: str, size, frames: int, scale: int, samples: int | None) -> str:
    scene.render.resolution_x, scene.render.resolution_y = size
    scene.render.resolution_percentage = scale
    scene.render.fps = FPS
    scene.frame_start, scene.frame_end = 1, frames
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGB"
    scene.render.image_settings.compression = 15
    try:
        scene.view_settings.view_transform = "AgX"
        scene.view_settings.look = "AgX - Medium High Contrast"
    except TypeError:
        pass
    if engine == "cycles":
        scene.render.engine = "CYCLES"
        scene.cycles.samples = samples or 96
        scene.cycles.use_denoising = True
        scene.cycles.max_bounces = 6
        prefs = bpy.context.preferences.addons["cycles"].preferences
        device = "CPU"
        for kind in ("OPTIX", "CUDA"):
            try:
                prefs.compute_device_type = kind
                prefs.get_devices()
                gpus = [d for d in prefs.devices if d.type == kind]
                if gpus:
                    for d in prefs.devices:
                        d.use = d.type == kind
                    device = f"GPU ({kind}: {', '.join(d.name for d in gpus)})"
                    break
            except TypeError:
                continue
        scene.cycles.device = "GPU" if device != "CPU" else "CPU"
        used = f"Cycles on {device}"
    else:
        scene.render.engine = "BLENDER_EEVEE_NEXT" if bpy.app.version < (5, 0) else "BLENDER_EEVEE"
        ee = scene.eevee
        ee.taa_render_samples = samples or 48
        ee.use_raytracing = True
        ee.ray_tracing_method = "SCREEN"
        ee.use_shadows = True
        with contextlib.suppress(AttributeError, TypeError):
            ee.shadow_pool_size = "256"  # MB of video memory for shadows; the GTX 750 Ti has 2 GB
        used = "EEVEE"
    _glow_compositor(scene)
    return used


def _glow_compositor(scene) -> None:
    """A soft glow around the glowing parts (fields, current), not around metal highlights."""
    scene.use_nodes = True
    nt = scene.node_tree
    nodes = nt.nodes
    rl = nodes.get("Render Layers") or nodes.new("CompositorNodeRLayers")
    comp = nodes.get("Composite") or nodes.new("CompositorNodeComposite")
    glare = nodes.new("CompositorNodeGlare")
    glare.glare_type = "FOG_GLOW"
    glare.quality = "HIGH"
    if "Threshold" in glare.inputs:  # Blender 4.5: settings are inputs
        glare.inputs["Threshold"].default_value = 2.5
    else:  # older versions
        glare.threshold = 2.5
        glare.size = 7
    nt.links.new(rl.outputs["Image"], glare.inputs["Image"])
    nt.links.new(glare.outputs["Image"], comp.inputs["Image"])


def render_frames(scene, frames, folder: Path, name: str, log) -> None:
    """Render frames one by one as PNGs; a frame only gets its final name when complete."""
    folder.mkdir(parents=True, exist_ok=True)
    done, times = 0, []
    for f in frames:
        final = folder / f"{f:04d}.png"
        if final.is_file() and final.stat().st_size > 0:
            done += 1
            continue
        scene.frame_set(f)
        tmp = folder / f"{f:04d}.tmp.png"
        scene.render.filepath = str(tmp)
        start = time.perf_counter()
        bpy.ops.render.render(write_still=True)
        seconds = time.perf_counter() - start
        os.replace(tmp, final)
        times.append(seconds)
        left = len(frames) - done - len(times)
        avg = sum(times[-20:]) / len(times[-20:])
        print(f"[{name}] frame {f}: {seconds:.1f} s  ({left} left, "
              f"about {left * avg / 60:.0f} min)", flush=True)  # fmt: skip
    if times:
        rest = times[1:] or times
        log(f"{name}: {len(times)} frames rendered (skipped {done} already done); first frame "
            f"{times[0]:.1f} s (includes shader setup), then {sum(rest) / len(rest):.1f} s per "
            "frame on average")  # fmt: skip
    else:
        log(f"{name}: all {done} frames were already rendered")


def encode(frames_dir: Path, out_file: Path, first: int, last: int, size, log) -> bool:
    """Join PNG frames first..last into an H.264 MP4 with Blender's own video encoder."""
    wanted = range(first, last + 1)
    missing = [f for f in wanted if not (frames_dir / f"{f:04d}.png").is_file()]
    if missing:
        log(f"not encoding {out_file.name}: {len(missing)} frames missing (first: {missing[0]})")
        return False
    count = len(wanted)
    scene = bpy.data.scenes.new(f"Encode {out_file.stem}")
    scene.render.resolution_x, scene.render.resolution_y = size
    scene.render.resolution_percentage = 100
    scene.render.fps = FPS
    scene.frame_start, scene.frame_end = 1, count
    scene.view_settings.view_transform = "Standard"
    editor = scene.sequence_editor_create()
    strips = getattr(editor, "strips", None) or editor.sequences
    strip = strips.new_image("frames", str(frames_dir / f"{first:04d}.png"), channel=1,
                             frame_start=1)  # fmt: skip
    for f in wanted[1:]:
        strip.elements.append(f"{f:04d}.png")
    strip.frame_final_duration = count
    scene.render.image_settings.file_format = "FFMPEG"
    scene.render.ffmpeg.format = "MPEG4"
    scene.render.ffmpeg.codec = "H264"
    scene.render.ffmpeg.constant_rate_factor = "PERC_LOSSLESS"
    scene.render.ffmpeg.ffmpeg_preset = "GOOD"
    scene.render.ffmpeg.audio_codec = "NONE"
    scene.render.filepath = str(out_file)
    with bpy.context.temp_override(scene=scene):
        bpy.ops.render.render(animation=True, scene=scene.name)
    log(f"clip written: {out_file}")
    return True
