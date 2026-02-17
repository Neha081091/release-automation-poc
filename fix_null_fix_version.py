import re

path = 'hybrid_step2_process_claude.py'
content = open(path).read()
fixed = re.sub(
    r"fix_version\s*=\s*ticket\.get\(['\"]fix_version['\"](?:,\s*['\"]['\"])?\)",
    'fix_version = ticket.get("fix_version") or ""',
    content
)
open(path, 'w').write(fixed)
print('Done - all fix_version assignments made null-safe')
