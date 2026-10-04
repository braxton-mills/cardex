#!/usr/bin/env python3
"""Builds Campi/Resources/Cars/*.usdz, the Cardex card's 3D cars, from Kenney's Car Kit (CC0, www.kenney.nl).

    python3 tools/make_car_models.py            # downloads the kit once into tools/.cache/

Each model's faces are split in two: "Paint" (the faces textured with the body's paint swatch of the kit's
shared colormap), which the app tints to the car's seen color, and "Trim" (everything else, textured).
Stdlib only, plus /usr/bin/usdzip and usdchecker (macOS).
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from collections import Counter
from pathlib import Path

KIT_URL = "https://kenney.nl/media/pages/assets/car-kit/1a312ec241-1775131960/kenney_car-kit.zip"
ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "tools" / ".cache" / "kenney_car-kit.zip"
OUT = ROOT / "Campi" / "Resources" / "Cars"

# CarBodyStyle raw value (CampiKit) -> Kenney model
MODELS = {
    "sedan": "sedan",
    "coupe": "sedan-sports",
    "suv": "suv-luxury",
    "offRoader": "suv",
    "hatchback": "hatchback-sports",
    "pickup": "truck",
    "van": "van",
    "boxTruck": "delivery",
}

# The colormap is 16 stripes across by 4 rows; row 1 holds the paint colors.
STRIPES, ROWS, PAINT_ROW = 16, 4, 1


def kit() -> zipfile.ZipFile:
    if not CACHE.exists():
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        print(f"downloading {KIT_URL}")
        with urllib.request.urlopen(KIT_URL) as r:
            CACHE.write_bytes(r.read())
    return zipfile.ZipFile(CACHE)


def parse_obj(text: str):
    v, vt, vn, faces = [], [], [], []   # faces: (group, [(vi, ti, ni), ...])
    group = ""
    for line in text.splitlines():
        p = line.split()
        if not p:
            continue
        if p[0] == "v":
            v.append(tuple(map(float, p[1:4])))
        elif p[0] == "vt":
            vt.append(tuple(map(float, p[1:3])))
        elif p[0] == "vn":
            vn.append(tuple(map(float, p[1:4])))
        elif p[0] == "g":
            group = p[1] if len(p) > 1 else ""
        elif p[0] == "f":
            corners = []
            for c in p[1:]:
                i = (c.split("/") + ["", ""])[:3]
                corners.append(tuple(int(x) - 1 if x else -1 for x in i))
            faces.append((group, corners))
    return v, vt, vn, faces


def swatch(vt, corners) -> tuple[int, int]:
    u = sum(vt[t][0] for _, t, _ in corners) / len(corners)
    w = sum(vt[t][1] for _, t, _ in corners) / len(corners)
    return min(int(u * STRIPES), STRIPES - 1), min(int((1 - w) * ROWS), ROWS - 1)


def mesh_usda(name: str, material: str, v, vt, vn, faces) -> str:
    counts, indices, points, normals, st = [], [], [], [], []
    remap: dict[int, int] = {}
    for _, corners in faces:
        counts.append(len(corners))
        for vi, ti, ni in corners:
            if vi not in remap:
                remap[vi] = len(points)
                points.append(v[vi])
            indices.append(remap[vi])
            normals.append(vn[ni] if ni >= 0 else (0, 1, 0))
            st.append(vt[ti] if ti >= 0 else (0, 0))

    def f3(xs):
        return ", ".join(f"({a:.5g}, {b:.5g}, {c:.5g})" for a, b, c in xs)

    def f2(xs):
        return ", ".join(f"({a:.5g}, {b:.5g})" for a, b in xs)

    return f"""
    def Mesh "{name}" (
        prepend apiSchemas = ["MaterialBindingAPI"]
    )
    {{
        int[] faceVertexCounts = [{", ".join(map(str, counts))}]
        int[] faceVertexIndices = [{", ".join(map(str, indices))}]
        point3f[] points = [{f3(points)}]
        normal3f[] normals = [{f3(normals)}] (
            interpolation = "faceVarying"
        )
        texCoord2f[] primvars:st = [{f2(st)}] (
            interpolation = "faceVarying"
        )
        uniform token subdivisionScheme = "none"
        rel material:binding = </Car/Materials/{material}>
    }}
"""


MATERIALS = """
    def Scope "Materials"
    {
        def Material "Trim"
        {
            token outputs:surface.connect = </Car/Materials/Trim/Surface.outputs:surface>

            def Shader "Surface"
            {
                uniform token info:id = "UsdPreviewSurface"
                color3f inputs:diffuseColor.connect = </Car/Materials/Trim/Texture.outputs:rgb>
                float inputs:roughness = 0.55
                float inputs:metallic = 0
                token outputs:surface
            }

            def Shader "Texture"
            {
                uniform token info:id = "UsdUVTexture"
                asset inputs:file = @colormap.png@
                float2 inputs:st.connect = </Car/Materials/Trim/UV.outputs:result>
                token inputs:wrapS = "clamp"
                token inputs:wrapT = "clamp"
                float3 outputs:rgb
            }

            def Shader "UV"
            {
                uniform token info:id = "UsdPrimvarReader_float2"
                string inputs:varname = "st"
                float2 outputs:result
            }
        }

        def Material "Paint"
        {
            token outputs:surface.connect = </Car/Materials/Paint/Surface.outputs:surface>

            def Shader "Surface"
            {
                uniform token info:id = "UsdPreviewSurface"
                color3f inputs:diffuseColor = (0.8, 0.25, 0.2)
                float inputs:roughness = 0.3
                float inputs:metallic = 0.2
                float inputs:clearcoat = 1
                float inputs:clearcoatRoughness = 0.1
                token outputs:surface
            }
        }
    }
"""


def build(z: zipfile.ZipFile, style: str, model: str, colormap: bytes, work: Path) -> Path:
    v, vt, vn, faces = parse_obj(z.read(f"Models/OBJ format/{model}.obj").decode())
    body = Counter(swatch(vt, c) for g, c in faces if g == "body" and swatch(vt, c)[1] == PAINT_ROW)
    if not body:
        sys.exit(f"{model}: no paint swatch found")
    paint_swatch = body.most_common(1)[0][0]
    paint = [f for f in faces if f[0] == "body" and swatch(vt, f[1]) == paint_swatch]
    trim = [f for f in faces if not (f[0] == "body" and swatch(vt, f[1]) == paint_swatch)]
    usda = f"""#usda 1.0
(
    defaultPrim = "Car"
    metersPerUnit = 1
    upAxis = "Y"
    doc = "{model} from Kenney's Car Kit (CC0, www.kenney.nl), split for Campi by tools/make_car_models.py"
)

def Xform "Car" (
    kind = "component"
)
{{{MATERIALS}{mesh_usda("Trim", "Trim", v, vt, vn, trim)}{mesh_usda("Paint", "Paint", v, vt, vn, paint)}}}
"""
    d = work / style
    d.mkdir()
    (d / f"{style}.usda").write_text(usda)
    (d / "colormap.png").write_bytes(colormap)
    out = OUT / f"{style}.usdz"
    subprocess.run(["usdzip", str(out), f"{style}.usda", "colormap.png"], cwd=d, check=True,
                   stdout=subprocess.DEVNULL)
    print(f"{out.relative_to(ROOT)}  ({model}: {len(paint)} paint / {len(trim)} trim faces, swatch {paint_swatch})")
    return out


def main() -> None:
    z = kit()
    colormap = z.read("Models/OBJ format/Textures/colormap.png")
    OUT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        outs = [build(z, style, model, colormap, Path(tmp)) for style, model in MODELS.items()]
    for out in outs:
        r = subprocess.run(["usdchecker", str(out)], capture_output=True, text=True)
        if r.returncode != 0:
            print(r.stdout + r.stderr)
            sys.exit(f"usdchecker failed for {out.name}")
    (OUT / "CREDITS.md").write_text(
        "# Car models\n\nFrom [Car Kit](https://kenney.nl/assets/car-kit) by Kenney (www.kenney.nl), "
        "licensed CC0 1.0 (public domain). Split into Paint and Trim meshes by `tools/make_car_models.py`.\n")


if __name__ == "__main__":
    main()
