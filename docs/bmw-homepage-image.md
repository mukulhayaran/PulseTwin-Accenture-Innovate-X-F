# BMW homepage photograph

The homepage uses `frontend/public/images/bmw-hero.webp` (1254 × 1254, 134,392 bytes).
The supplied `C:/Users/perso/Downloads/BMW.jpeg` was the edit target and remains unchanged.
The treatment was created with the built-in Imagegen tool, then converted to WebP
with the frontend's existing Sharp dependency at quality 88. No new dependencies
or external image requests are required at runtime.

The interactive graphics are separate React/SVG/CSS layers in
`frontend/components/landing/VehicleHero.tsx` and `VehicleHero.module.css`.
They illustrate Quality, Assembly, and Traceability, and are explicitly labeled
as a concept view rather than live telemetry. Controls support mouse hover,
keyboard focus/activation, touch, and reduced motion.

The refined layout caps the panel at 560px, crops excess sky in a 3:2 viewport,
and fades the photograph's edges into the white page canvas with CSS. Its title,
description and compact underlined controls sit below the image. The image and
interactive markers retain the same square coordinate system so the crop does
not displace the points. The homepage header and sections share a 1280px container.
This layout refinement leaves the saved photograph unchanged.

## Final image-edit prompt

Use case: lighting-weather. Asset type: production website hero photograph for
PulseTwin, a digital twin for automotive assembly, styled in BMW blue #1c69d4,
warm navy #1a2129, clean white, and brushed silver. Input image 1 is the EDIT TARGET.
Apply a restrained premium automotive editorial color grade to this EXACT photo.
Preserve the exact silver BMW, every body detail, grille, emblem, headlights,
license plate, wheels, camera perspective, coast, and road. Keep the square
composition, car position, car size and ample sky above it unchanged; car remains
entirely visible. Change only color and lighting: cooler slate-blue sky and coastal
scene with gently deepened navy-blue cloud shadows toward the top, slightly
desaturated earth and road tones, crisp natural metallic silver body with subtle
cool-blue reflections, polished contrast without crushing car detail. It must look
like the user's real photo professionally graded, not an illustration or another
car. Keep lower half bright enough to show the full vehicle. No text, no graphics,
no UI, no new objects, no digital effects, no watermarks. The website will place
interactive graphics over this separately. Output one square photograph.
