import json
import os
import sqlite3
import time
from contextlib import closing
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st
from dotenv import load_dotenv
from groq import Groq


load_dotenv()
st.set_page_config(page_title="LibraryGenie | AI SQL Agent", page_icon="📚", layout="wide")

DB_PATH = Path(__file__).with_name("library.db")
MODEL_NAME = "openai/gpt-oss-20b"
MAX_RESULT_ROWS = 50
SOURCE_LABEL = "Source: books table (library.db)"

st.markdown("""
<style>
.block-container {max-width: 1250px; padding-top: 2rem;}
[data-testid="stMetric"] {background: rgba(91,122,255,0.075); border: 1px solid rgba(128,140,176,0.18); border-radius: 14px; padding: 14px;}
[data-testid="stChatMessage"] {border: 1px solid rgba(128,140,176,0.16); border-radius: 14px;}
</style>
""", unsafe_allow_html=True)


def get_connection():
    uri = f"{DB_PATH.resolve().as_uri()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")
    return connection


def get_schema():
    with closing(get_connection()) as connection:
        names = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
        schema = {}
        for item in names:
            name = item["name"]
            quoted_name = '"' + name.replace('"', '""') + '"'
            details = connection.execute(f"PRAGMA table_info({quoted_name})").fetchall()
            schema[name] = [f"{column['name']} ({column['type']})" for column in details]
    return schema


def get_categories():
    with closing(get_connection()) as connection:
        rows = connection.execute("SELECT DISTINCT category FROM books ORDER BY category").fetchall()
    return [row["category"] for row in rows]


def create_sql_plan(client, question, schema_text, categories, previous_sql="", error_text=""):
    response_format = {
        "type": "json_schema",
        "json_schema": {
            "name": "library_sql_plan",
            "strict": True,
            "schema": {
                "type": "object",
                "properties": {
                    "sql": {"type": "string"},
                    "assumption": {"type": "string"},
                    "followups": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["sql", "assumption", "followups"],
                "additionalProperties": False,
            },
        },
    }
    system_prompt = f"""
You are LibraryGenie, a beginner-friendly SQLite data analyst for a school library.
Students may ask in English or Roman Urdu (for example "Science category mein kitni books hain?").
Translate the user question into ONE SQLite SELECT statement using only this schema:
{schema_text}
The category column contains exactly these values: {", ".join(categories)}.
Rules:
- Return a single read-only SELECT or WITH...SELECT statement. Never produce write operations, PRAGMA, ATTACH, or multiple statements.
- Use only existing table and column names, never invent columns.
- When filtering by category, use the exact category value shown above.
- For book listings add LIMIT 20 unless the user requests a smaller limit.
- For counts and summaries, compute aggregates across ALL matching rows before applying any display limit.
- "Most borrowed" means ORDER BY times_borrowed DESC, then book_id ASC.
- "Not available" or "zero available copies" means available_copies = 0.
- "Total borrowings" for a category means SUM(times_borrowed) grouped by category.
- If the question is not about this library database, use an empty sql string and explain briefly in assumption.
- Never pretend to have run a query. Python will execute your SQL separately.
- Give two short follow-up questions related to available data.
"""
    correction = f"Previous SQL: {previous_sql}\nDatabase error: {error_text}\nFix the SQL using the schema." if error_text else ""
    response = client.chat.completions.create(
        model=MODEL_NAME,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": question + "\n" + correction},
        ],
        temperature=0,
        max_completion_tokens=1600,
        reasoning_effort="low",
        reasoning_format="hidden",
        response_format=response_format,
    )
    return json.loads(response.choices[0].message.content or "{}")


def execute_read_only_sql(sql):
    statement = sql.strip().rstrip(";").strip()
    if not statement or not statement.lower().startswith(("select ", "select\n", "with ", "with\n")):
        raise ValueError("Only read-only SELECT statements are allowed.")
    if ";" in statement:
        raise ValueError("Only one SQL statement is allowed.")
    if len(statement) > 4500:
        raise ValueError("The query is too long for this demo.")

    allowed_functions = {
        "count", "avg", "sum", "min", "max", "round", "lower", "upper", "length", "substr",
        "coalesce", "ifnull", "nullif", "abs", "total", "group_concat", "trim", "replace",
        "instr", "printf", "typeof",
    }

    def only_reads(action, arg1, arg2, database, source):
        if database not in (None, "main"):
            return sqlite3.SQLITE_DENY
        if action in (sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ):
            return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_FUNCTION and str(arg2 or "").lower() in allowed_functions:
            return sqlite3.SQLITE_OK
        return sqlite3.SQLITE_DENY

    connection = get_connection()
    connection.set_authorizer(only_reads)
    started = time.monotonic()
    connection.set_progress_handler(lambda: int(time.monotonic() - started > 3), 1000)
    try:
        cursor = connection.execute(statement)
        rows = cursor.fetchmany(MAX_RESULT_ROWS + 1)
        column_names = [column[0] for column in cursor.description]
        limited = len(rows) > MAX_RESULT_ROWS
        dataframe = pd.DataFrame([tuple(row) for row in rows[:MAX_RESULT_ROWS]], columns=column_names)
        return dataframe, limited
    except sqlite3.Error as error:
        raise ValueError(f"Database rejected the query: {error}") from error
    finally:
        connection.close()


def explain_results(client, question, sql, dataframe, limited):
    preview = dataframe.head(15).to_csv(index=False)
    extra = "More rows exist than the displayed preview." if limited else "All returned rows are displayed."
    prompt = f"""
You are a friendly librarian explaining actual SQL results to school students in simple English.
Explain what the table shows. Use only facts explicitly supported by the SQL output.
Do not invent numbers, books, authors, trends, or explanations.
If there are no results, say that no matching records were found.
If the result is truncated, state the limitation.
User question: {question}
Executed SQL: {sql}
Result preview (CSV):
{preview}
{extra}
Give a concise answer of 2 to 4 sentences.
"""
    response = client.chat.completions.create(
        model=MODEL_NAME,
        messages=[{"role": "user", "content": prompt}],
        temperature=0,
        max_completion_tokens=1200,
        reasoning_effort="low",
        reasoning_format="hidden",
    )
    return response.choices[0].message.content or "No explanation was generated."


SORT_OPTIONS = {
    "Most borrowed": "times_borrowed DESC, book_id ASC",
    "Least borrowed": "times_borrowed ASC, book_id ASC",
    "Fewest copies available": "available_copies ASC, book_id ASC",
    "Title (A-Z)": "title ASC",
}

fragment = getattr(st, "fragment", lambda func: func)  # reruns only the dashboard on widget clicks


def load_filtered_books(categories, only_out_of_stock, max_copies, min_borrowed, search, sort_by):
    """Build a parameterized, read-only SELECT from the dashboard filters."""
    clauses, params = [], []
    if categories:
        clauses.append(f"category IN ({','.join('?' * len(categories))})")
        params += categories
    else:
        clauses.append("1 = 0")
    if only_out_of_stock:
        clauses.append("available_copies = 0")
    else:
        clauses.append("available_copies <= ?")
        params.append(max_copies)
    clauses.append("times_borrowed >= ?")
    params.append(min_borrowed)
    if search:
        clauses.append("(LOWER(title) LIKE ? OR LOWER(author) LIKE ?)")
        params += [f"%{search.lower()}%"] * 2
    sql = "SELECT * FROM books WHERE " + " AND ".join(clauses) + " ORDER BY " + SORT_OPTIONS[sort_by]
    with closing(get_connection()) as connection:
        return pd.read_sql_query(sql, connection, params=params), sql, params


@fragment
def render_dashboard():
    with closing(get_connection()) as connection:
        all_books = pd.read_sql_query("SELECT * FROM books ORDER BY book_id", connection)
    all_categories = sorted(all_books["category"].unique().tolist())
    max_stock = int(all_books["available_copies"].max())
    max_borrowed = int(all_books["times_borrowed"].max())

    st.subheader("Interactive Library Dashboard")
    st.caption("Change any filter and every number, chart and table below updates instantly.")

    f1, f2 = st.columns([2, 1])
    selected_categories = f1.multiselect("Categories", all_categories, default=all_categories)
    sort_by = f2.selectbox("Sort books by", list(SORT_OPTIONS))

    f3, f4, f5 = st.columns(3)
    search = f3.text_input("Search title or author", placeholder="e.g. python, Khan, space")
    min_borrowed = f4.slider("Minimum times borrowed", 0, max_borrowed, 0)
    only_out_of_stock = f5.toggle("Only books with 0 copies available", value=False)
    max_copies = max_stock
    if not only_out_of_stock:
        max_copies = st.slider("Show books with at most this many copies available", 0, max_stock, max_stock)

    filtered, sql, params = load_filtered_books(
        selected_categories, only_out_of_stock, max_copies, min_borrowed, search.strip(), sort_by
    )

    if filtered.empty:
        st.info("No books match these filters. Try widening them.")
        with st.expander("SQL behind this view"):
            st.code(sql, language="sql")
        return

    filtered = filtered.assign(
        stock_status=filtered["available_copies"].map(
            lambda copies: "Out of stock" if copies == 0 else ("Low stock" if copies <= 2 else "In stock")
        )
    )

    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Books shown", len(filtered), delta=f"{len(filtered) - len(all_books)} vs all", border=True)
    k2.metric("Total borrowings", int(filtered["times_borrowed"].sum()), border=True)
    k3.metric("Copies available", int(filtered["available_copies"].sum()), border=True)
    k4.metric("Out of stock", int((filtered["available_copies"] == 0).sum()), border=True)

    chart_tab, top_tab, scatter_tab, table_tab, detail_tab = st.tabs(
        ["📊 By category", "🏆 Top books", "🔍 Popularity vs stock", "📋 Table", "📖 Book details"]
    )

    with chart_tab:
        metric_label = st.radio(
            "Measure", ["Total borrowings", "Number of books", "Copies available"], horizontal=True
        )
        by_category = filtered.groupby("category", as_index=False).agg(
            **{
                "Total borrowings": ("times_borrowed", "sum"),
                "Number of books": ("book_id", "count"),
                "Copies available": ("available_copies", "sum"),
            }
        )
        chart = (
            alt.Chart(by_category)
            .mark_bar(cornerRadiusTopLeft=6, cornerRadiusTopRight=6)
            .encode(
                x=alt.X("category:N", sort="-y", title="Category"),
                y=alt.Y(f"{metric_label}:Q"),
                color=alt.Color("category:N", legend=None),
                tooltip=["category", "Total borrowings", "Number of books", "Copies available"],
            )
            .properties(height=320)
        )
        st.altair_chart(chart, width="stretch")

    with top_tab:
        if len(filtered) > 3:
            top_n = st.slider("How many books?", 3, min(20, len(filtered)), min(10, len(filtered)))
        else:
            top_n = len(filtered)
        top_books = filtered.nlargest(top_n, "times_borrowed")
        chart = (
            alt.Chart(top_books)
            .mark_bar()
            .encode(
                x=alt.X("times_borrowed:Q", title="Times borrowed"),
                y=alt.Y("title:N", sort="-x", title=None),
                color=alt.Color("category:N", title="Category"),
                tooltip=["title", "author", "category", "times_borrowed", "available_copies"],
            )
            .properties(height=max(220, 32 * len(top_books)))
        )
        st.altair_chart(chart, width="stretch")

    with scatter_tab:
        st.caption("Books in the top-left corner are very popular but have few copies left. Hover over a dot for details.")
        chart = (
            alt.Chart(filtered)
            .mark_circle(size=140, opacity=0.8)
            .encode(
                x=alt.X("available_copies:Q", title="Copies available"),
                y=alt.Y("times_borrowed:Q", title="Times borrowed"),
                color=alt.Color("category:N", title="Category"),
                tooltip=["title", "author", "category", "available_copies", "times_borrowed"],
            )
            .properties(height=380)
            .interactive()
        )
        st.altair_chart(chart, width="stretch")

    with table_tab:
        st.dataframe(
            filtered[["book_id", "title", "author", "category", "available_copies", "times_borrowed", "stock_status"]],
            hide_index=True,
            width="stretch",
            height=360,
            column_config={
                "times_borrowed": st.column_config.ProgressColumn(
                    "Times borrowed", min_value=0, max_value=max_borrowed, format="%d"
                ),
            },
        )
        st.download_button(
            "⬇️ Download filtered books (CSV)",
            filtered.to_csv(index=False).encode("utf-8"),
            file_name="library_filtered_books.csv",
            mime="text/csv",
        )

    with detail_tab:
        chosen = st.selectbox("Pick a book", filtered["title"].tolist())
        book = filtered[filtered["title"] == chosen].iloc[0]
        rank = int((all_books["times_borrowed"] > book["times_borrowed"]).sum()) + 1
        d1, d2 = st.columns(2)
        d1.markdown(f"### {book['title']}\n**Author:** {book['author']}  \n**Category:** {book['category']}  \n**Book ID:** {book['book_id']}")
        d2.metric("Copies available", int(book["available_copies"]), border=True)
        d2.metric("Times borrowed", int(book["times_borrowed"]), delta=f"Rank #{rank} of {len(all_books)}", border=True)
        if book["stock_status"] == "Out of stock":
            st.error("All copies are currently borrowed.")
        elif book["stock_status"] == "Low stock":
            st.warning("Only a few copies are left.")
        else:
            st.success("Copies are available.")

    with st.expander("SQL behind this view (read-only, parameterized)"):
        st.code(sql, language="sql")
        st.caption(f"Parameters: {params}")
    st.caption(SOURCE_LABEL)


st.title("📚 LibraryGenie")
st.caption("AI SQL Agent for a school library | Ask in English or Roman Urdu and verify the real database results")

if not DB_PATH.exists():
    st.error("library.db was not found. Run: python create_library_db.py")
    st.stop()

with st.sidebar:
    st.header("Agent Control Center")
    api_key = st.text_input("Groq API Key", value=os.getenv("GROQ_API_KEY", ""), type="password")
    st.caption(f"Model: {MODEL_NAME}")
    st.success("Database access: Read-only (SELECT only)")
    st.info("Educational synthetic records only.")
    if st.button("Clear conversation", width="stretch"):
        st.session_state.history = []
        st.session_state.pending_question = ""
        st.rerun()

with closing(get_connection()) as connection:
    metrics = pd.read_sql_query(
        "SELECT COUNT(*) AS books, COUNT(DISTINCT category) AS categories, "
        "SUM(available_copies) AS available, SUM(times_borrowed) AS borrowed FROM books",
        connection,
    ).iloc[0]

m1, m2, m3, m4 = st.columns(4)
m1.metric("Books", int(metrics["books"]), border=True)
m2.metric("Categories", int(metrics["categories"]), border=True)
m3.metric("Copies available", int(metrics["available"]), border=True)
m4.metric("Total borrowings", int(metrics["borrowed"]), border=True)

ask_tab, data_tab, concept_tab = st.tabs(["🧠 Ask LibraryGenie", "📊 Interactive Dashboard", "🛡️ How the Agent Works"])

if "history" not in st.session_state:
    st.session_state.history = []
if "pending_question" not in st.session_state:
    st.session_state.pending_question = ""

with ask_tab:
    st.subheader("Ask a question in English or Roman Urdu")
    st.caption("Try: How many books are in the library? | Science category mein kitni books hain? | Which books have zero available copies?")
    e1, e2, e3 = st.columns(3)
    if e1.button("🏆 5 most borrowed books", width="stretch"):
        st.session_state.pending_question = "Show the 5 most borrowed books"
    if e2.button("🚫 Zero available copies", width="stretch"):
        st.session_state.pending_question = "Which books have zero available copies?"
    if e3.button("📈 Top borrowed category", width="stretch"):
        st.session_state.pending_question = "Which category has the highest total borrowings?"

    for message in st.session_state.history:
        with st.chat_message("user"):
            st.markdown(message["question"])
        with st.chat_message("assistant"):
            st.markdown(message["answer"])
            with st.expander("See generated SQL and database evidence"):
                st.code(message["sql"], language="sql")
                st.dataframe(pd.DataFrame(message["rows"]), hide_index=True, width="stretch")
                st.caption(SOURCE_LABEL)

    typed_question = st.chat_input("Ask a question about the library books...")
    question = st.session_state.pending_question or typed_question
    st.session_state.pending_question = ""

    if question:
        if not api_key:
            st.warning("Add your Groq API Key in the sidebar first.")
        else:
            try:
                client = Groq(api_key=api_key)
                schema = get_schema()
                schema_text = "\n".join(f"{table}: {', '.join(columns)}" for table, columns in schema.items())
                categories = get_categories()
                with st.status("LibraryGenie is working", expanded=True) as status:
                    st.write("1. Reading the database schema")
                    plan = create_sql_plan(client, question, schema_text, categories)
                    sql = plan.get("sql", "").strip()
                    if not sql:
                        raise ValueError(plan.get("assumption", "Please ask about the library books."))
                    st.write("2. SQL proposed by the model")
                    st.code(sql, language="sql")
                    st.write("3. Checking permissions and running a read-only query")
                    try:
                        dataframe, limited = execute_read_only_sql(sql)
                    except ValueError as first_error:
                        if not str(first_error).startswith("Database rejected the query:"):
                            raise
                        st.write("4. Database returned a SQL error. Asking the agent to correct it once")
                        corrected = create_sql_plan(client, question, schema_text, categories, sql, str(first_error))
                        sql = corrected.get("sql", "").strip()
                        st.code(sql, language="sql")
                        dataframe, limited = execute_read_only_sql(sql)
                        plan = corrected
                    st.write(f"4. Database returned {len(dataframe)} displayed rows")
                    st.write("5. Explaining the real database output")
                    explanation = explain_results(client, question, sql, dataframe, limited)
                    status.update(label="Database research completed", state="complete", expanded=False)

                with st.chat_message("user"):
                    st.markdown(question)
                with st.chat_message("assistant"):
                    st.markdown(explanation)

                st.subheader("Database evidence")
                st.dataframe(dataframe, hide_index=True, width="stretch")
                st.caption(SOURCE_LABEL)
                if limited:
                    st.warning(f"Preview limited to {MAX_RESULT_ROWS} rows. Add filters or aggregates for a smaller result.")
                with st.expander("View generated SQL and agent actions", expanded=True):
                    st.code(sql, language="sql")
                    if plan.get("assumption"):
                        st.caption(f"Assumption: {plan['assumption']}")
                    st.write("Executed against: library.db / books table / SQLite read-only connection")
                if not dataframe.empty:
                    st.download_button(
                        "⬇️ Download result CSV",
                        dataframe.to_csv(index=False).encode("utf-8"),
                        file_name="librarygenie_result.csv",
                        mime="text/csv",
                    )
                if plan.get("followups"):
                    st.caption("Try next: " + " | ".join(plan["followups"][:2]))
                st.session_state.history.append({
                    "question": question,
                    "answer": explanation,
                    "sql": sql,
                    "rows": dataframe.to_dict(orient="records"),
                })
            except Exception as error:
                st.error(f"Could not complete the question: {error}")
                st.caption("Try a shorter question. Make sure library.db and your Groq API key are available.")

with data_tab:
    render_dashboard()
    with st.expander("Inspect database schema"):
        for table_name, columns in get_schema().items():
            st.write(f"**{table_name}**: {', '.join(columns)}")

with concept_tab:
    st.subheader("How LibraryGenie works")
    st.markdown("**Question → Database schema → AI-generated SQL → Python safety checks → SQLite → Results → AI explanation**")
    st.info("The AI proposes SQL, but Python decides whether it can run. Only SELECT is allowed, and the database is opened in read-only mode.")
    st.markdown("**This is not training a new AI model.** We connect an existing language model to a database tool.")
    st.warning("Never share your API key. Keep it in a .env file or type it in the sidebar.")