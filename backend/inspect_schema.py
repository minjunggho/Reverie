import sqlite3
conn = sqlite3.connect('reverie_dev.sqlite3')
cur = conn.cursor()
print(cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='characters'").fetchone())
for row in cur.execute('PRAGMA table_info(characters)'):
    print(row)
conn.close()
