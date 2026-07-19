# -*- coding: utf-8 -*-
"""Pure functions for output filename generation.

Independent of any UI state or FilmProcessor internals.
Can be called from a Web API or mobile backend.
"""

import re


def generate_output_filename(
    output_file: str,
    info_roll: str = "",
    info_camera: str = "",
    info_film: str = "",
    info_shoot_date: str = "",
    default_dir: str = ".",
) -> str:
    """Generate output filename from config fields.

    If the user provided a custom filename, return it unchanged.
    Otherwise, derive a name from the filled info fields.

    Args:
        output_file: The user-provided output filename.
        info_roll: Roll number.
        info_camera: Camera name.
        info_film: Film type.
        info_shoot_date: Shooting date.
        default_dir: Fallback directory for the output path.

    Returns:
        Full output path string.
    """
    default_names = {'filmsheet_output.jpg', 'filmsheet_output.png', 'filmsheet_output.jpeg'}
    if output_file.strip() not in default_names:
        # User customized the filename
        return output_file

    parts = [p.strip() for p in [info_roll, info_camera, info_film, info_shoot_date] if p.strip()]
    if not parts:
        return output_file

    name = '_'.join(parts)
    name = re.sub(r'[\\/*?:"<>|]', '_', name)

    # Determine output path
    if output_file:
        dirname = output_file if '/' in output_file or '\\' in output_file else default_dir
        _, ext = _split_ext(output_file)
        return f"{dirname}/{name}{ext}"
    return f"{default_dir}/{name}.jpg"


def _split_ext(filename: str) -> tuple[str, str]:
    """Split filename and extension, handling multiple dots."""
    name, ext = filename.rsplit('.', 1)
    return name, f'.{ext}'
