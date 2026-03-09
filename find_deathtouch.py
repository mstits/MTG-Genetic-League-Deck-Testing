import engine.game
with open(engine.game.__file__, 'r') as f:
    lines = f.readlines()
    for i, line in enumerate(lines):
        if 'deathtouch_damaged' in line:
            print(f"Line {i+1}: {line.strip()}")
