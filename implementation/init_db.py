import sqlite3
import os
import sys

SCHEMA_SQL = """
DROP TABLE IF EXISTS enrollments;
DROP TABLE IF EXISTS students;
DROP TABLE IF EXISTS courses;

CREATE TABLE students (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    email TEXT NOT NULL UNIQUE,
    cohort TEXT NOT NULL
);

CREATE TABLE courses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL UNIQUE,
    instructor TEXT NOT NULL
);

CREATE TABLE enrollments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id INTEGER NOT NULL,
    course_id INTEGER NOT NULL,
    grade REAL,
    status TEXT NOT NULL CHECK (status IN ('active', 'completed')),
    FOREIGN KEY (student_id) REFERENCES students(id) ON DELETE CASCADE,
    FOREIGN KEY (course_id) REFERENCES courses(id) ON DELETE CASCADE
);
"""

SEED_SQL = """
-- Insert students
INSERT INTO students (name, email, cohort) VALUES 
('Alice Smith', 'alice@university.edu', 'A1'),
('Bob Jones', 'bob@university.edu', 'A1'),
('Charlie Brown', 'charlie@university.edu', 'B2'),
('Diana Prince', 'diana@university.edu', 'B2'),
('Evan Wright', 'evan@university.edu', 'C3');

-- Insert courses
INSERT INTO courses (title, instructor) VALUES 
('Introduction to Computer Science', 'Dr. Alan Turing'),
('Database Systems', 'Dr. Edgar Codd'),
('Linear Algebra', 'Dr. Gilbert Strang'),
('American History', 'Dr. Howard Zinn');

-- Insert enrollments
INSERT INTO enrollments (student_id, course_id, grade, status) VALUES 
(1, 1, 95.5, 'completed'),
(1, 2, 88.0, 'completed'),
(2, 1, 91.0, 'completed'),
(2, 3, 76.5, 'active'),
(3, 2, 84.0, 'completed'),
(3, 4, 92.0, 'completed'),
(4, 1, 98.0, 'completed'),
(4, 3, 90.0, 'completed'),
(5, 4, 82.5, 'active');
"""

def create_database(db_path: str = "local.db") -> str:
    """
    Initializes the SQLite database with the standard schema and seed data.
    """
    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.cursor()
        # Enable foreign keys for validation
        cursor.execute("PRAGMA foreign_keys = ON;")
        cursor.executescript(SCHEMA_SQL)
        cursor.executescript(SEED_SQL)
        conn.commit()
    finally:
        conn.close()
    return os.path.abspath(db_path)

def create_postgres_database(dsn: str):
    """
    Initializes a PostgreSQL database with schema and seed data if available.
    """
    try:
        import psycopg2
    except ImportError:
        print("psycopg2 is not installed. Cannot initialize PostgreSQL database.")
        return

    # Translate SQLite dialect to PostgreSQL dialect
    pg_schema = SCHEMA_SQL.replace("AUTOINCREMENT", "GENERATED ALWAYS AS IDENTITY")
    # For DROP TABLE IF EXISTS, PostgreSQL supports CASCADE
    pg_schema = pg_schema.replace("DROP TABLE IF EXISTS enrollments;", "DROP TABLE IF EXISTS enrollments CASCADE;")
    pg_schema = pg_schema.replace("DROP TABLE IF EXISTS students;", "DROP TABLE IF EXISTS students CASCADE;")
    pg_schema = pg_schema.replace("DROP TABLE IF EXISTS courses;", "DROP TABLE IF EXISTS courses CASCADE;")
    
    conn = psycopg2.connect(dsn)
    try:
        with conn.cursor() as cursor:
            cursor.execute(pg_schema)
            cursor.execute(SEED_SQL)
        conn.commit()
        print("PostgreSQL database initialized successfully.")
    except Exception as e:
        conn.rollback()
        print(f"Error initializing PostgreSQL database: {e}")
        raise e
    finally:
        conn.close()

if __name__ == "__main__":
    db_path = "local.db"
    if len(sys.argv) > 1:
        db_path = sys.argv[1]
    
    abs_path = create_database(db_path)
    print(f"Database initialized at: {abs_path}")
