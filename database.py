import datetime
import sqlite3

from config import DATABASE_PATH, TIMEZONE


conn = sqlite3.connect(DATABASE_PATH)
conn.execute("PRAGMA journal_mode=WAL")
cursor = conn.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS members(
user_id INTEGER PRIMARY KEY,
patrol_votes INTEGER DEFAULT 0,
cant_make INTEGER DEFAULT 0,
aop_votes INTEGER DEFAULT 0,
patrol_attended INTEGER DEFAULT 0,
patrol_skipped INTEGER DEFAULT 0,
aop_skipped INTEGER DEFAULT 0
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS patrol_days(
day TEXT,
attendance INTEGER
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS aop_stats(
area TEXT,
day TEXT
)
""")

cursor.execute("PRAGMA table_info(aop_stats)")
aop_columns = [col[1] for col in cursor.fetchall()]
if "day" not in aop_columns:
    cursor.execute("ALTER TABLE aop_stats ADD COLUMN day TEXT")

cursor.execute("PRAGMA table_info(patrol_days)")
patrol_columns = [col[1] for col in cursor.fetchall()]
if "cancelled" not in patrol_columns:
    cursor.execute("ALTER TABLE patrol_days ADD COLUMN cancelled INTEGER DEFAULT 0")
if "cant_make" not in patrol_columns:
    cursor.execute("ALTER TABLE patrol_days ADD COLUMN cant_make INTEGER DEFAULT 0")

cursor.execute("""
CREATE TABLE IF NOT EXISTS settings(
key TEXT PRIMARY KEY,
value TEXT
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS activity_log(
user_id INTEGER,
action TEXT,
day TEXT
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS session_state(
key TEXT PRIMARY KEY,
value TEXT
)
""")

conn.commit()


def ensure_member(user_id):
    cursor.execute("INSERT OR IGNORE INTO members(user_id) VALUES(?)", (user_id,))
    conn.commit()


def record_stat(user_id, column):
    ensure_member(user_id)
    cursor.execute(f"UPDATE members SET {column} = {column} + 1 WHERE user_id = ?", (user_id,))
    conn.commit()


def log_activity(user_id, action):
    today = datetime.datetime.now(TIMEZONE).strftime("%Y-%m-%d")
    cursor.execute("INSERT INTO activity_log(user_id, action, day) VALUES(?, ?, ?)", (user_id, action, today))
    conn.commit()


def get_inactive_reason(user_id):
    cursor.execute("SELECT action, day FROM activity_log WHERE user_id = ? ORDER BY day DESC", (user_id,))
    rows = cursor.fetchall()

    if not rows:
        return "No activity ever"

    last_date = datetime.datetime.strptime(rows[0][1], "%Y-%m-%d").date()
    days_ago = (datetime.datetime.now(TIMEZONE).date() - last_date).days

    recent_actions = [r[0] for r in rows[:20]]

    only_cant_make = all(a == "cant_make" for a in recent_actions)
    mostly_cant_make = recent_actions.count("cant_make") > len(recent_actions) * 0.7

    if only_cant_make:
        return f"Only marks can't make it (last: {days_ago}d ago)"
    elif mostly_cant_make:
        return f"Mostly marks can't make it (last: {days_ago}d ago)"
    else:
        return f"Stopped responding ({days_ago}d ago)"


def patrol_day_exists(day):
    cursor.execute("SELECT 1 FROM patrol_days WHERE day = ?", (day,))
    return cursor.fetchone() is not None


def aop_stat_exists(day):
    cursor.execute("SELECT 1 FROM aop_stats WHERE day = ?", (day,))
    return cursor.fetchone() is not None


def get_cutoff(period):
    from config import Period
    if period == Period.last_2_weeks:
        return (datetime.datetime.now(TIMEZONE).date() - datetime.timedelta(days=14)).strftime("%Y-%m-%d")
    return None


def period_label(period):
    return period.value
