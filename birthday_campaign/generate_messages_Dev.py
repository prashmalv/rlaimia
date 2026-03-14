import csv
import pandas as pd

# ===============================
# FILE PATHS
# ===============================
PERSONA_FILE = "data/Persona_Dummy_Data.csv"
TMINUS10_FILE = "data/birthday_templates_tminus10.csv"
TDAY_FILE = "data/birthday_templates_tday.csv"
OUTPUT_FILE = "output/birthday_messages_output.csv"

# ===============================
# SAFE CSV READER (FOR PERSONA DATA)
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
# PERSONA NORMALIZATION
# ===============================
def normalize_persona(persona: str) -> str:
    if not isinstance(persona, str):
        return ""

    persona = persona.lower().replace('"', '').replace("'", "")
    if "(" in persona:
        persona = persona.split("(")[0]

    return persona.strip()

# ===============================
# FIRST NAME EXTRACTION
# ===============================
def get_first_name(full_name: str) -> str:
    if not isinstance(full_name, str):
        return ""
    return full_name.strip().split()[0]

# ===============================
# RAW TEMPLATE LOADER (CRITICAL FIX)
# ===============================
def load_raw_templates(path):
    """
    Reads raw template lines directly from CSV.
    This bypasses Pandas completely for template text.
    """
    templates = []

    with open(path, "r", encoding="utf-8-sig", errors="ignore") as f:
        lines = f.readlines()

    # Skip header
    for line in lines[1:]:
        clean = line.strip()
        if not clean:
            continue

        clean = clean.replace('"', '').replace("'", "")

        # Split by TAB first, else comma
        parts = clean.split("\t") if "\t" in clean else clean.split(",")

        if len(parts) < 3:
            continue

        age_persona = parts[1].strip()
        message_parts = parts[2:]

        # Build message from remaining parts
        message = " ".join(
            p.strip() for p in message_parts
            if p.strip() and p.strip() != ","
        )

        if message:
            templates.append({
                "persona_norm": normalize_persona(age_persona),
                "template_text": message
            })

    return templates

# ===============================
# LOAD DATA
# ===============================
people_df = read_csv_safe(PERSONA_FILE)
tminus10_templates = load_raw_templates(TMINUS10_FILE)
tday_templates = load_raw_templates(TDAY_FILE)

print("People columns:", people_df.columns.tolist())
print("Unique personas (people):", people_df["Persona"].unique())
print("Loaded T-10 templates:", len(tminus10_templates))

# ===============================
# MESSAGE GENERATOR
# ===============================
def generate_message(name, persona, phase):
    persona_key = normalize_persona(persona)

    if phase == "T_MINUS_10":
        templates = tminus10_templates
    elif phase == "T_DAY":
        templates = tday_templates
    else:
        return None

    matches = [t for t in templates if t["persona_norm"] == persona_key]
    if not matches:
        return None

    template_text = matches[0]["template_text"]
    return template_text.replace("<first_name>", name)

# ===============================
# MAIN PROCESSING (DEV MODE)
# ===============================
output_rows = []

for _, row in people_df.iterrows():
    phase = "T_MINUS_10"  # 🔥 FORCE CAMPAIGN FOR DEMO

    first_name = get_first_name(row["Name"])
    persona = row["Persona"]

    message = generate_message(
        name=first_name,
        persona=persona,
        phase=phase
    )

    if message:
        output_rows.append({
            "Full Name": row["Name"],
            "First Name": first_name,
            "Age": row["Age"],
            "Persona": row["Persona"],
            "Campaign Phase": phase,
            "Final Message": message
        })

# ===============================
# SAVE OUTPUT
# ===============================
output_df = pd.DataFrame(output_rows)
output_df.to_csv(OUTPUT_FILE, index=False)

print("DEV MODE: Birthday messages generated successfully.")
print(f"Total messages generated: {len(output_df)}")
