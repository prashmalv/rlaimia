import csv
import pandas as pd
from datetime import datetime

# ===============================
# FILE PATHS
# ===============================
PERSONA_FILE = "data/Persona_Dummy_Data.csv"
TMINUS10_FILE = "data/birthday_templates_tminus10.csv"
TDAY_FILE = "data/birthday_templates_tday.csv"
OUTPUT_FILE = "output/birthday_messages_output.csv"

# ===============================
# SAFE CSV READER
# ===============================
def read_csv_safe(path):
    with open(path, "r", encoding="utf-8-sig", errors="ignore") as f:
        sample = f.read(2048)

    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=[",", "\t", ";", "|"])
        delimiter = dialect.delimiter
    except Exception:
        delimiter = "\t"

    df = pd.read_csv(
        path,
        encoding="utf-8-sig",
        sep=delimiter,
        engine="python",
        quoting=3,
        on_bad_lines="skip"
    )

    df.columns = (
        df.columns.astype(str)
        .str.replace('"', '', regex=False)
        .str.replace("'", '', regex=False)
        .str.replace('\xa0', '', regex=False)
        .str.replace(',', '', regex=False)
        .str.strip()
    )

    return df

# ===============================
# LOAD DATA
# ===============================
people_df = read_csv_safe(PERSONA_FILE)
tminus10_df = read_csv_safe(TMINUS10_FILE)
tday_df = read_csv_safe(TDAY_FILE)

# ===============================
# HELPERS
# ===============================
def get_first_name(full_name):
    return full_name.strip().split()[0]

def get_campaign_phase(birthday_date):
    today = datetime.today().date()
    birthday = pd.to_datetime(birthday_date).date()
    days_left = (birthday - today).days

    if days_left == 10:
        return "T_MINUS_10"
    elif days_left == 0:
        return "T_DAY"
    else:
        return None

def generate_message(name, persona, phase):
    if phase == "T_MINUS_10":
        templates = tminus10_df[tminus10_df["Age Persona"] == persona]
    elif phase == "T_DAY":
        templates = tday_df[tday_df["Age Persona"] == persona]
    else:
        return None

    if templates.empty:
        return None

    template_text = str(templates.sample(1).iloc[0]["Text"])
    return template_text.replace("<first_name>", name)

# ===============================
# MAIN (PRODUCTION MODE)
# ===============================
output_rows = []

for _, row in people_df.iterrows():
    # 🚨 REQUIRED IN PROD
    birthday_date = row.get("Birthday Date")
    if not birthday_date:
        continue

    phase = get_campaign_phase(birthday_date)
    if not phase:
        continue

    message = generate_message(
        name=get_first_name(row["Name"]),
        persona=row["Persona"],
        phase=phase
    )

    if message:
        output_rows.append({
            "Name": row["Name"],
            "Age": row["Age"],
            "Persona": row["Persona"],
            "Campaign Phase": phase,
            "Final Message": message
        })

output_df = pd.DataFrame(output_rows)
output_df.to_csv(OUTPUT_FILE, index=False)

print("PROD MODE: Birthday messages generated successfully.")
print(f"Total messages generated: {len(output_df)}")
