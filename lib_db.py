import sqlite3
from pathlib import Path


db_path = Path(__file__).with_name("library.db")
connection = sqlite3.connect(db_path)
cursor = connection.cursor()

cursor.execute("DROP TABLE IF EXISTS books")
cursor.execute("""
CREATE TABLE books (
    book_id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    author TEXT NOT NULL,
    category TEXT NOT NULL,
    available_copies INTEGER NOT NULL CHECK (available_copies >= 0),
    times_borrowed INTEGER NOT NULL CHECK (times_borrowed >= 0)
)
""")

# (title, author, category, available_copies, times_borrowed) - synthetic practice records
books = [
    # Science
    ("The Wonders of Physics", "Dr. Amina Qureshi", "Science", 4, 52),
    ("Chemistry Made Simple", "Prof. Imran Haider", "Science", 0, 67),
    ("Biology Around Us", "Sara Nadeem", "Science", 3, 41),
    ("Journey Through Space", "Dr. Kamran Yousuf", "Science", 2, 88),
    ("The Human Body Explained", "Dr. Hina Farooq", "Science", 0, 74),
    ("Experiments for Young Minds", "Bilal Anwar", "Science", 5, 33),
    ("Earth and Its Secrets", "Maryam Siddiqui", "Science", 1, 29),
    ("Electricity and Magnets", "Prof. Tariq Mehmood", "Science", 6, 18),
    # Fiction
    ("The Lost Lantern", "Zainab Rauf", "Fiction", 2, 95),
    ("Midnight in Lahore", "Faisal Chaudhry", "Fiction", 0, 81),
    ("The Silent River", "Noor Fatima", "Fiction", 4, 60),
    ("Tales of the Mountain Village", "Asad Mirza", "Fiction", 3, 47),
    ("The Clockmaker's Daughter", "Ayesha Rehman", "Fiction", 1, 72),
    ("Secret of the Old Library", "Hamza Iqbal", "Fiction", 5, 38),
    ("The Brave Little Sparrow", "Rabia Shah", "Fiction", 7, 22),
    ("Shadows over Karachi", "Omar Khalid", "Fiction", 2, 55),
    # History
    ("Ancient Civilizations of the Indus", "Dr. Saleem Akhtar", "History", 3, 36),
    ("The Story of Pakistan", "Prof. Nasreen Baig", "History", 0, 90),
    ("Great Empires of the World", "Adeel Hussain", "History", 4, 44),
    ("Heroes Who Changed History", "Mehwish Tariq", "History", 2, 51),
    ("The Silk Road Chronicles", "Dr. Waqar Ali", "History", 5, 27),
    ("World Wars Simplified", "Usman Ghani", "History", 1, 63),
    ("Medieval Times", "Sana Javed", "History", 6, 15),
    ("Leaders of the Subcontinent", "Prof. Rashid Latif", "History", 3, 40),
    # Technology
    ("Learn Python Step by Step", "Hassan Raza", "Technology", 2, 99),
    ("Introduction to Robotics", "Dr. Farah Naz", "Technology", 3, 58),
    ("The Internet Explained", "Danish Malik", "Technology", 0, 70),
    ("Building Your First Website", "Iqra Saleem", "Technology", 4, 62),
    ("Artificial Intelligence for Beginners", "Dr. Zeeshan Ahmed", "Technology", 1, 92),
    ("Cyber Safety for Students", "Laiba Khan", "Technology", 5, 31),
    ("Computer Basics", "Talha Mahmood", "Technology", 8, 20),
    ("Data and Databases Made Easy", "Kinza Arshad", "Technology", 2, 49),
]

cursor.executemany(
    "INSERT INTO books (title, author, category, available_copies, times_borrowed) VALUES (?, ?, ?, ?, ?)",
    books,
)
connection.commit()

count = cursor.execute("SELECT COUNT(*) FROM books").fetchone()[0]
print(f"Database ready: {db_path}")
print(f"Total books: {count}")
print("Sample records:")
for row in cursor.execute("SELECT book_id, title, category, available_copies, times_borrowed FROM books LIMIT 5"):
    print(row)

connection.close()