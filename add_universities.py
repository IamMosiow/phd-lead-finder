import os
import re
import time
import requests
import pandas as pd

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

XLSX_FILE = "List of emails.xlsx"
CSV_FILE = "List of emails.xlsx - Sheet1.csv"
FILE_TO_LOAD = XLSX_FILE if os.path.exists(XLSX_FILE) else CSV_FILE

# Number of new engineering universities to append per run
BATCH_SIZE = 10

# Excluded ISO-2 Country Codes: USA + All African Countries (+ Iran)
AFRICAN_COUNTRIES = {
    "DZ", "AO", "BJ", "BW", "BF", "BI", "CV", "CM", "CF", "TD", "KM", "CG",
    "CD", "CI", "DJ", "EG", "GQ", "ER", "SZ", "ET", "GA", "GM", "GH", "GN",
    "GW", "KE", "LS", "LR", "LY", "MG", "MW", "ML", "MR", "MU", "YT", "MA",
    "MZ", "NA", "NE", "NG", "RE", "RW", "SH", "ST", "SN", "SC", "SL", "SO",
    "ZA", "SS", "SD", "TZ", "TG", "TN", "UG", "EH", "ZM", "ZW"
}
EXCLUDED_COUNTRIES = {"US", "IR"} | AFRICAN_COUNTRIES

def send_telegram_html(text):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("[!] Missing Telegram credentials.")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }
    requests.post(url, json=payload, timeout=20)

def send_telegram_document(file_path, caption=""):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID or not os.path.exists(file_path):
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendDocument"
    try:
        with open(file_path, "rb") as doc:
            requests.post(
                url,
                data={"chat_id": TELEGRAM_CHAT_ID, "caption": caption},
                files={"document": doc},
                timeout=35
            )
    except Exception as e:
        print(f"[!] Document send error: {e}")

def normalize_name(name):
    """Normalizes university names for duplicate checking."""
    name = str(name).lower().strip()
    name = re.sub(r'\(.*?\)', '', name)
    name = re.sub(r'[^a-z0-9\s]', '', name)
    return re.sub(r'\s+', ' ', name).strip()

def has_engineering_focus(inst_obj):
    """Checks if the university has Engineering in its name, topics, or concepts."""
    name_lower = (inst_obj.get("display_name") or "").lower()
    if any(k in name_lower for k in ["engineering", "technology", "polytechnic", "politecnico", "technische"]):
        return True

    # Check OpenAlex topics
    for t in inst_obj.get("topics", []):
        field_name = (t.get("field", {}).get("display_name") or "").lower()
        topic_name = (t.get("display_name") or "").lower()
        if "engineering" in field_name or "engineering" in topic_name:
            return True

    # Check OpenAlex legacy x_concepts
    for c in inst_obj.get("x_concepts", []):
        concept_name = (c.get("display_name") or "").lower()
        if "engineering" in concept_name and c.get("score", 0) > 30:
            return True

    return False

def fetch_new_engineering_universities(existing_norm_names, target_count=10):
    """Fetches top universities with Engineering outside USA and Africa from OpenAlex."""
    headers = {"User-Agent": "mailto:phd_lead_bot@academic.org"}
    added_universities = []

    # Paginate through top global educational institutions sorted by publication output
    for page in range(1, 15):
        if len(added_universities) >= target_count:
            break

        url = (
            f"https://api.openalex.org/institutions?"
            f"filter=type:education&sort=works_count:desc&per-page=50&page={page}"
        )
        try:
            res = requests.get(url, headers=headers, timeout=20).json()
            results = res.get("results", [])
            if not results:
                break

            for inst in results:
                if len(added_universities) >= target_count:
                    break

                country_code = (inst.get("country_code") or "").upper()
                if not country_code or country_code in EXCLUDED_COUNTRIES:
                    continue

                uni_name = inst.get("display_name", "").strip()
                if not uni_name:
                    continue

                norm = normalize_name(uni_name)
                if norm in existing_norm_names:
                    continue

                if not has_engineering_focus(inst):
                    continue

                country_name = inst.get("geo", {}).get("country") or country_code
                added_universities.append({
                    "University": uni_name,
                    "Country": country_name,
                    "Website": inst.get("homepage_url", "")
                })
                existing_norm_names.add(norm)
                print(f"[+] Found: {uni_name} ({country_name})")

            time.sleep(1)
        except Exception as e:
            print(f"[!] Error fetching page {page}: {e}")
            break

    return added_universities

def main():
    print(f"Loading existing file: {FILE_TO_LOAD}")
    if not os.path.exists(FILE_TO_LOAD):
        print(f"[!] File {FILE_TO_LOAD} not found!")
        return

    df = pd.read_csv(FILE_TO_LOAD) if FILE_TO_LOAD.endswith(".csv") else pd.read_excel(FILE_TO_LOAD)

    # Ensure standard columns exist
    for col in ["University", "Country", "Checked?", "Checked_General?"]:
        if col not in df.columns:
            df[col] = ""

    # Build set of existing normalized university names
    existing_names = set()
    for u in df["University"].dropna().tolist():
        for part in re.split(r'\band\b|/', str(u)):
            norm = normalize_name(part)
            if norm:
                existing_names.add(norm)

    print(f"Loaded {len(existing_names)} existing unique universities.")

    new_unis = fetch_new_engineering_universities(existing_names, target_count=BATCH_SIZE)

    if not new_unis:
        print("[-] No new matching universities found in this cycle.")
        send_telegram_html("ℹ️ <b>University Finder:</b> No new universities added in this run.")
        return

    # Append new rows to the end of the DataFrame
    new_rows = []
    for item in new_unis:
        row_dict = {col: "" for col in df.columns}
        row_dict["University"] = item["University"]
        row_dict["Country"] = item["Country"]
        row_dict["Checked?"] = "NO"
        row_dict["Checked_General?"] = "NO"
        if "Note" in df.columns and item.get("Website"):
            row_dict["Note"] = item["Website"]
        new_rows.append(row_dict)

    new_df = pd.DataFrame(new_rows)
    df = pd.concat([df, new_df], ignore_index=True)

    # Save to BOTH .xlsx and .csv so they stay 100% synced
    try:
        df.to_excel(XLSX_FILE, index=False)
        print(f"[✓] Saved {XLSX_FILE}")
    except Exception as e:
        print(f"[!] Error saving {XLSX_FILE}: {e}")

    try:
        df.to_csv(CSV_FILE, index=False)
        print(f"[✓] Saved {CSV_FILE}")
    except Exception as e:
        print(f"[!] Error saving {CSV_FILE}: {e}")

    # Send Telegram report
    msg = f"🏛 <b>Added {len(new_unis)} New Engineering Universities (Excl. USA & Africa):</b>\n\n"
    for idx, u in enumerate(new_unis, 1):
        msg += f"{idx}. <b>{u['University']}</b> ({u['Country']})\n"
    msg += "\n<i>Appended to the end of your tracking files for professor discovery.</i>"

    send_telegram_html(msg)
    send_telegram_document(XLSX_FILE, caption="💾 Updated tracking sheet with new universities.")

if __name__ == "__main__":
    main()
