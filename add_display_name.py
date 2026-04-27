from backend.utils.db import get_db_path
import sqlite3

db_path = get_db_path()
conn = sqlite3.connect(db_path)
cursor = conn.cursor()

cursor.execute("PRAGMA table_info(users)")
columns = [col[1] for col in cursor.fetchall()]
if 'display_name' not in columns:
    cursor.execute("ALTER TABLE users ADD COLUMN display_name VARCHAR(100)")
    cursor.execute("UPDATE users SET display_name = username")
    print("Added display_name to users")
else:
    print("display_name already exists")

conn.commit()
conn.close()
