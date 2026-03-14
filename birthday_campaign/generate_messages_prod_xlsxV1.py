import pandas as pd
import random
from datetime import datetime, timedelta
from pathlib import Path

# ===============================
# FILE PATHS
# ===============================
PERSON_FILE = "data/Persona_Dummy_Data.xlsx"
TEMPLATE_FILE = "data/Sample Templates- mia.xlsx"
CONFIG_FILE = "data/campaign_config.xlsx"

OUTPUT_FILE = "output/birthday_messages_output.xlsx"
AUDIT_FILE = "output/audit_log.xlsx"
ERROR_FILE = "output/error_log.xlsx"

Path("output").mkdir(exist_ok=True)

TODAY = datetime.today().date()

# ===============================
# HELPERS
# ===============================
def get_first_name(full_name):
    return str(full_name).strip().split()[0]

def normalize_persona(text):
    if not isinstance(text, str):
        return ""
    text = text.lower()
    if "(" in text:
        text = text.split("(")[0]
    return text.strip()

def detect_name_column(df):
    for col in df.columns:
        if col.lower().strip() in ["name", "full name", "customer name"]:
            return col
    raise ValueError("Name column not found")

def get_campaign_phase(dob):
    dob = pd.to_datetime(dob).date()
    dob_this_year = dob.replace(year=TODAY.year)

    if dob_this_year == TODAY:
        return "T_DAY"
    elif dob_this_year - timedelta(days=10) == TODAY:
        return "T_MINUS_10"
    return None

# ===============================
# LOAD CONFIG (FEATURE 4)
# ===============================
config_df = pd.read_excel(CONFIG_FILE)
enabled_phases = set(
    config_df[config_df["enabled"] == True]["campaign_phase"]
)

# ===============================
# LOAD PERSON DATA
# ===============================
people_df = pd.read_excel(PERSON_FILE)
people_df.columns = people_df.columns.str.strip()

NAME_COL = detect_name_column(people_df)

people_df["persona_norm"] = people_df["Persona"].apply(normalize_persona)
people_df["first_name"] = people_df[NAME_COL].apply(get_first_name)

# ===============================
# LOAD TEMPLATES
# ===============================
templates = {
    "T_MINUS_10": pd.read_excel(TEMPLATE_FILE, sheet_name="BirthdayT-10"),
    "T_DAY": pd.read_excel(TEMPLATE_FILE, sheet_name="BirthdayT0")
}

for phase, df in templates.items():
    df.columns = df.columns.str.strip()
    df["persona_norm"] = df["Age Persona"].apply(normalize_persona)
    templates[phase] = df

# ===============================
# LOAD AUDIT LOG (FEATURE 1 & 2)
# ===============================
if Path(AUDIT_FILE).exists():
    audit_df = pd.read_excel(AUDIT_FILE)
else:
    audit_df = pd.DataFrame(
        columns=["Full Name", "Campaign Phase", "Execution Date"]
    )

# ===============================
# MESSAGE GENERATOR
# ===============================
def generate_message(first_name, persona_norm, phase):
    df = templates.get(phase)
    matched = df[df["persona_norm"] == persona_norm]
    if matched.empty:
        return None
    text = random.choice(matched["Text"].dropna().tolist())
    return text.replace("<first_name>", first_name)

# ===============================
# MAIN PROCESSING
# ===============================
output_rows = []
error_rows = []

for _, row in people_df.iterrows():
    try:
        phase = get_campaign_phase(row["DOB"])

        if not phase or phase not in enabled_phases:
            continue

        # 🔒 Idempotency check
        already_sent = (
            (audit_df["Full Name"] == row[NAME_COL]) &
            (audit_df["Campaign Phase"] == phase) &
            (audit_df["Execution Date"] == TODAY)
        ).any()

        if already_sent:
            continue

        message = generate_message(
            row["first_name"],
            row["persona_norm"],
            phase
        )

        if not message:
            continue

        output_rows.append({
            "Full Name": row[NAME_COL],
            "First Name": row["first_name"],
            "Age": row["Age"],
            "Persona": row["Persona"],
            "DOB": row["DOB"],
            "Campaign Phase": phase,
            "Execution Date": TODAY,
            "Final Message": message
        })

        audit_df.loc[len(audit_df)] = [
            row[NAME_COL], phase, TODAY
        ]

    except Exception as e:
        error_rows.append({
            "Full Name": row.get(NAME_COL),
            "Error": str(e),
            "Timestamp": datetime.now()
        })

# ===============================
# SAVE OUTPUTS
# ===============================
pd.DataFrame(output_rows).to_excel(OUTPUT_FILE, index=False)
audit_df.to_excel(AUDIT_FILE, index=False)

if error_rows:
    pd.DataFrame(error_rows).to_excel(ERROR_FILE, index=False)

print("PROD RUN COMPLETE")
print(f"Messages generated today: {len(output_rows)}")
print(f"Errors logged: {len(error_rows)}")
