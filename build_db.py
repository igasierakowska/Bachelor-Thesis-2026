
import argparse
import re
import sqlite3
from pathlib import Path

import pandas as pd

DEFAULT_OUT = "wavedata.db"

# Each source (file name or Excel sheet name) is matched to one of these tables.
CANONICAL = ["EXP_DET", "EXP_ENT", "ORD_DET", "ORD_ENT", "VAG_DET"]

# Columns indexed wherever they appear (faster joins/filters as data grows).
KEY_COLUMNS = {"wavenumber", "vagno", "ordno", "expno", "detno", "ref", "operator"}


def clean_col(name: str) -> str:
    name = str(name).strip()
    name = re.sub(r"[^0-9A-Za-z]+", "_", name)
    name = re.sub(r"_+", "_", name).strip("_")
    return name or "col"


def canonical_table(source_name: str) -> str:
    upper = re.sub(r"[^A-Za-z0-9]+", "_", str(source_name)).upper()
    for token in CANONICAL:
        if token in upper:
            return token
    if "PICKING" in upper:
        return "Picking_Wave"
    return clean_col(source_name)  # fallback: just clean the name


def detect_sep(path: Path) -> str:
    with open(path, encoding="utf-8-sig") as f:
        first = f.readline()
    return ";" if first.count(";") >= first.count(",") else ","


def tidy(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = [clean_col(c) for c in df.columns]

    # Strip whitespace padding from text columns (fixes 'H-06-13   ' vs 'H-06-13').
    text_cols = [
        c for c in df.columns
        if df[c].dtype == object or pd.api.types.is_string_dtype(df[c])
    ]
    for col in text_cols:
        df[col] = df[col].map(lambda v: v.strip() if isinstance(v, str) else v)

    # Convert comma-decimal text columns (e.g. '8,5') to real numbers.
    # Only when every value is an integer or comma-decimal AND a comma appears,
    # so ID columns like 43175 and product codes like 8620FLX are left alone.
    for col in text_cols:
        vals = df[col].dropna().astype(str).str.strip()
        vals = vals[vals != ""]
        if len(vals) and vals.str.fullmatch(r"\d+(,\d+)?").all() and vals.str.contains(",").any():
            df[col] = pd.to_numeric(
                df[col].astype(str).str.strip().str.replace(",", ".", regex=False),
                errors="coerce",
            )
    return df


def add_indexes(conn, table, columns) -> None:
    cur = conn.cursor()
    for col in columns:
        if col.lower() in KEY_COLUMNS:
            idx = f"idx_{table}_{col}".lower()
            cur.execute(f'CREATE INDEX IF NOT EXISTS "{idx}" ON "{table}" ("{col}")')
    conn.commit()


def main() -> None:
    ap = argparse.ArgumentParser(description="Build SQLite DB from wave data files.")
    ap.add_argument("--folder", default=".", help="Folder containing the data files.")
    ap.add_argument("--out", default=DEFAULT_OUT)
    args = ap.parse_args()

    folder = Path(args.folder)
    out_path = Path(args.out)
    if out_path.exists():
        out_path.unlink()

    conn = sqlite3.connect(out_path)
    summary = []  # (source, table, rows, cols)

    # Excel files: each sheet -> a table -------------------------------------
    for xlsx in sorted(folder.glob("*.xlsx")):
        if xlsx.name.startswith("~$"):
            continue  # skip Excel lock/temp files
        xl = pd.ExcelFile(xlsx)
        sheets = xl.sheet_names
        for sheet in sheets:
            df = tidy(pd.read_excel(xl, sheet_name=sheet))
            # Single-sheet workbook: name from the file. Multi-sheet: from the sheet.
            src = xlsx.stem if len(sheets) == 1 else sheet
            table = canonical_table(src)
            df.to_sql(table, conn, if_exists="replace", index=False)
            add_indexes(conn, table, df.columns)
            summary.append((f"{xlsx.name} [{sheet}]", table, len(df), len(df.columns)))

    # CSV files: one table each ----------------------------------------------
    for csv in sorted(folder.glob("*.csv")):
        df = tidy(pd.read_csv(csv, sep=detect_sep(csv), encoding="utf-8-sig"))
        table = canonical_table(csv.stem)
        df.to_sql(table, conn, if_exists="replace", index=False)
        add_indexes(conn, table, df.columns)
        summary.append((csv.name, table, len(df), len(df.columns)))

    conn.close()

    print(f"\nBuilt {out_path}\n")
    print(f"{'source file':<34}{'-> table':<16}{'rows':>9}{'cols':>6}")
    print("-" * 65)
    for source, table, rows, cols in summary:
        print(f"{source:<34}{table:<16}{rows:>9,}{cols:>6}")
    if not summary:
        print("No .csv or .xlsx files found in this folder.")


if __name__ == "__main__":
    main()
