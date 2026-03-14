"""
Generates /app/data/SampleTemplatesmia.xlsx programmatically.
Run during Docker build to avoid relying on binary file transfer.
"""
import os
from pathlib import Path
import openpyxl

OUT_PATH = Path(__file__).parent / "SampleTemplatesmia.xlsx"

TEMPLATES = {
    "BirthdayT-10": [
        {
            "Age Persona": "Gen Z Starter (20\u201324)",
            "Text": (
                "\nMessage:\n Hey <first_name>,\n Your birthday\u2019s around the corner, and that\u2019s reason enough to pause for a second.\n"
                "Whether you feel like keeping it simple or choosing something that feels a little more you, we\u2019ve put together styles that feel right for where you are right now.\n"
                "Take a look whenever you feel like it. No rush.\n <short_link>\n"
            ),
        },
        {
            "Age Persona": "Young Achiever (25-29)",
            "Text": (
                "\nHi <first_name>,\nWith your birthday coming up, this is a good moment to pause and think about what feels right for you this year.\n\n"
                "Whether it\u2019s something you\u2019ve been meaning to buy for yourself or a piece that simply fits your style today, our birthday edits bring together contemporary design and everyday elegance.\n\n"
                "You can explore online, preview how styles look on you with Virtual Try-On, or visit a Mia store whenever it suits you.\n\n"
                "Discover styles curated for you\n <short_link>\nPrefer seeing it in person?\nFind a Mia store near you <link>\n\n\u2014 Mia\n"
            ),
        },
        {
            "Age Persona": "Life Builder (30-34)",
            "Text": (
                "\nDear <first_name>,\nWith your birthday approaching, this is a quiet moment to reflect on what feels meaningful to you this year.\n\n"
                "Many choose this time to mark the occasion with a piece that feels personal today and continues to hold relevance over time. Our birthday selection is curated with that sensibility in mind.\n\n"
                "You may explore the collection online, preview how styles look on you with Virtual Try-On, or visit a Mia store at your convenience.\n\n"
                "Explore pieces chosen for moments that matter\n <short_link>\n\nPrefer seeing it in person?\nFind a Mia store near you <link>\n\n\u2014 Mia by Tanishq\n"
            ),
        },
        {
            "Age Persona": "Family & Value Seeker (35-45)",
            "Text": (
                "\nDear <first_name>,\nWith your birthday approaching, this is a moment many choose to celebrate with intention - selecting something that reflects their taste and the confidence they\u2019ve built over time.\n\n"
                "Our birthday edit brings together refined design and craftsmanship meant to be worn, enjoyed, and returned to - not just saved for special occasions.\n\n"
                "You may explore the collection online, preview how pieces look on you with Virtual Try-On, or visit a Mia store whenever it suits you.\n\n"
                "Explore jewellery chosen with confidence <short_link>\nPrefer seeing it in person?\nFind a Mia store near you <link>\n\n\u2014 Team Mia\n"
            ),
        },
        {
            "Age Persona": "Mature Optimiser (45+)",
            "Text": (
                "\nDear <first_name>,\nAs your birthday approaches, this is a moment many choose to mark with something truly considered - a piece that reflects discernment, individuality, and a life well lived.\n\n"
                "Our birthday edition has been curated for those who value refined design and lasting presence - jewellery chosen not to follow trends, but to stand apart from them.\n\n"
                "You may explore the collection online, preview how pieces look on you with Virtual Try-On, or visit a Mia store at a time that suits you best.\n\n"
                "Discover pieces chosen for those who know their style\n <short_link>\nPrefer seeing it in person?\nFind a Mia store near you <link>\n\n\u2014 Mia by Tanishq"
            ),
        },
    ],
    "BirthdayT0": [
        {
            "Age Persona": "Gen Z Starter (20\u201324)",
            "Text": (
                "\nHi <first_name>,\nHappy Birthday \u2728\n\n"
                "Today doesn\u2019t need plans or overthinking - it\u2019s just a good day to do something for yourself. \n\n"
                "If you feel like treating yourself, we\u2019ve got styles that fit into your everyday and still feel special when you wear them.\n\n"
                "Explore online, see how they look on you with Virtual Try-On, or drop by a Mia store whenever it suits you.\n\n"
                "Pick something you\u2019ll love wearing\n <short_link>\n\nPrefer seeing it in person?\nFind a Mia store near you: <link>\n\n\u2014 Always with you, Mia\n"
            ),
        },
        {
            "Age Persona": "Young Achiever (25-29)",
            "Text": (
                "\nHi <first_name>,\nHappy Birthday\u2728\n\n"
                "Some milestones don\u2019t need a big plan - just a choice that feels considered. If you\u2019re thinking of marking today with something special, our collections are designed to fit seamlessly into your everyday while still standing out.\n\n"
                "Enjoy up to 10% off on select jewellery during your birthday period.\n\n"
                "Choose a piece that feels right for you\n<short_link>\n\nPrefer seeing it in person?\nFind a Mia store near you <link>\n\n- Mia\n"
            ),
        },
        {
            "Age Persona": "Life Builder (30-34)",
            "Text": (
                "\nDear <first_name>,\nWarm birthday wishes from Mia\u2728\n\n"
                "Every year brings moments worth acknowledging in a way that feels thoughtful and lasting. \n\n"
                "If you\u2019re considering marking today with something special, our collections are designed to balance contemporary style with timeless appeal.\n\n"
                "A birthday benefit is available on select jewellery during this period.\n\n"
                "Choose a piece that holds meaning beyond today\n <short_link>\n\nPrefer seeing it in person?\nFind a Mia store near you <link>\n\n\u2014 Mia by Tanishq\n"
            ),
        },
        {
            "Age Persona": "Family & Value Seeker (35-45)",
            "Text": (
                "\nDear <first_name>,\nWishing you a very Happy Birthday\u2728\n\n"
                "Some celebrations call for a choice that feels assured - a piece that complements your style today and continues to feel right beyond the occasion. Our collections are designed with this sensibility at heart.\n\n"
                "A birthday privilege is available on select jewellery during this period.\n\n"
                "Choose a piece that reflects your sense of style\n <short_link>\n\nPrefer seeing it in person?\nFind a Mia store near you <link>\n\n\u2014 Team Mia\n"
            ),
        },
        {
            "Age Persona": "Mature Optimiser (45+)",
            "Text": (
                "\nDear <first_name>,\nWarm and thoughtful birthday wishes\u2728\n\n"
                "Some birthdays are best marked with intention - celebrating taste, confidence, and a clear sense of what feels right.\n\n"
                "Our birthday selection is curated for individuals who choose with intention - pieces that don\u2019t ask for attention, yet always hold it.\n\n"
                "A birthday privilege is available on select jewellery should you wish to mark the day.\n\n"
                "Choose a piece that reflects who you are <short_link>\nPrefer seeing it in person?\nFind a Mia store near you <link>\n\n\u2014 Mia by Tanishq\n"
            ),
        },
    ],
}


def create_template():
    wb = openpyxl.Workbook()
    wb.remove(wb.active)  # remove default sheet

    for sheet_name, rows in TEMPLATES.items():
        ws = wb.create_sheet(sheet_name)
        ws.append(["S.no", "Age Persona", "Text"])
        for row in rows:
            ws.append([None, row["Age Persona"], row["Text"]])

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    wb.save(str(OUT_PATH))
    print(f"Created {OUT_PATH} ({OUT_PATH.stat().st_size} bytes)")


if __name__ == "__main__":
    create_template()
