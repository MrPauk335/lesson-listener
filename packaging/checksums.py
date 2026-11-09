"""Generate checksums only for distributable release assets."""
import hashlib
from pathlib import Path

out = Path(__file__).resolve().parent.parent / 'release'
lines = []
for path in sorted(out.iterdir()):
    if not path.name.startswith('LessonListener-') or not path.is_file():
        continue
    with path.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    lines.append(f'{digest}  {path.name}\n')
if not lines:
    raise SystemExit('No release assets found.')
(out / 'SHA256SUMS.txt').write_text(''.join(lines), encoding='utf-8')
print(''.join(lines), end='')
