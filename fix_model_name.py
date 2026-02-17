path = 'hybrid_step2_process_claude.py'
content = open(path).read()
fixed = content.replace('claude-3-5-sonnet-20241022', 'claude-opus-4-6')
open(path, 'w').write(fixed)
print('Done - model updated to claude-opus-4-6')
