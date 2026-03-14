import pandas as pd
import random

# ===============================
# FILE PATHS
# ===============================
PERSON_FILE = "data/Persona_Dummy_Data.xlsx"
TEMPLATE_FILE = "data/SampleTemplatesmia.xlsx"
OUTPUT_FILE = "output/birthday_messages_output.xlsx"

# ===============================
# HELPERS
# ===============================
def get_first_name(full_name: str) -> str:
    if not isinstance(full_name, str):
        return ""
    return full_name.strip().split()[0]

def normalize_persona(text: str) -> str:
    """
    'Gen Z Starter (20–24)' -> 'gen z starter'
    'Gen Z Starter'        -> 'gen z starter'
    """
    if not isinstance(text, str):
        return ""

    text = text.lower()
    if "(" in text:
        text = text.split("(")[0]
    return text.strip()

def detect_name_column(df: pd.DataFrame) -> str:
    possible_cols = [
        "full name",
        "name",
        "customer name",
        "person name"
    ]

    for col in df.columns:
        if col.lower().strip() in possible_cols:
            return col

    raise ValueError(f"No name column found. Columns: {df.columns.tolist()}")

# ===============================
# LOAD PERSON DATA
# ===============================
people_df = pd.read_excel(PERSON_FILE)
people_df.columns = people_df.columns.str.strip()

NAME_COL = detect_name_column(people_df)

people_df["persona_norm"] = people_df["Persona"].apply(normalize_persona)
people_df["first_name"] = people_df[NAME_COL].apply(get_first_name)

print("Detected name column:", NAME_COL)

# ===============================
# LOAD TEMPLATE DATA (BOTH SHEETS)
# ===============================
templates = {
    "T_MINUS_10": pd.read_excel(TEMPLATE_FILE, sheet_name="BirthdayT-10"),
    "T_DAY": pd.read_excel(TEMPLATE_FILE, sheet_name="BirthdayT0")
}

for phase, df in templates.items():
    df.columns = df.columns.str.strip()
    df["persona_norm"] = df["Age Persona"].apply(normalize_persona)
    templates[phase] = df

print("Loaded template phases:", list(templates.keys()))

# ===============================
# MESSAGE GENERATOR
# ===============================
def generate_message(first_name, persona_norm, phase):
    df = templates.get(phase)
    if df is None:
        return None

    matched = df[df["persona_norm"] == persona_norm]
    if matched.empty:
        return None

    template_text = random.choice(matched["Text"].dropna().tolist())
    return template_text.replace("<first_name>", first_name)

# ===============================
# MAIN PROCESSING (DEV MODE)
# ===============================
output_rows = []

for _, row in people_df.iterrows():
    for phase in ["T_MINUS_10", "T_DAY"]:   # 🔥 BOTH PHASES

        message = generate_message(
            first_name=row["first_name"],
            persona_norm=row["persona_norm"],
            phase=phase
        )

        if message:
            output_rows.append({
                "Full Name": row[NAME_COL],
                "First Name": row["first_name"],
                "Age": row["Age"],
                "Persona": row["Persona"],
                "Campaign Phase": phase,
                "Final Message": message
            })

# ===============================
# SAVE OUTPUT
# ===============================
output_df = pd.DataFrame(output_rows)
output_df.to_excel(OUTPUT_FILE, index=False)

print("DEV MODE (XLSX): Birthday messages generated successfully.")
print(f"Total messages generated: {len(output_df)}")
