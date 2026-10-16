import os
import re
import time
import requests
import pandas as pd

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

XLSX_FILE = "List of emails.xlsx"
CSV_FILE = "List of emails.xlsx - Sheet1.csv"
FILE_NAME = XLSX_FILE if os.path.exists(XLSX_FILE) else CSV_FILE

# 1. Localization Groups (Used only when 'Checked?' != 'YES')
LOCALIZATION_GROUPS = [
    (
        "Localization",
        ["ultra-wideband", "indoor localization", "indoor positioning", "channel impulse response", "CSI sensing", "TDoA"]
    ),
    (
        "Wireless Sensing",
        ["wireless localization", "RF sensing", "sensor fusion", "ISAC", "6G localization", "pedestrian dead reckoning"]
    )
]

# 2. Broader CV Groups
GENERAL_GROUPS = [
    (
        "FPGA & Edge AI",
        [
            "FPGA neural network accelerator", "deep learning FPGA", "TinyML",
            "embedded machine learning", "hardware accelerator VHDL",
            "low-power digital systems", "edge AI computing", "digital signal processing FPGA"
        ]
    ),
    (
        "Vision & Autonomous Systems",
        [
            "autonomous vehicle perception", "CARLA autonomous simulation",
            "deep learning object detection", "computer vision embedded",
            "camera LiDAR sensor fusion", "autonomous driving perception"
        ]
    ),
    (
        "Biomedical & Acoustic Signals",
        [
            "otoacoustic emissions", "biomedical signal processing",
            "auditory diagnostic signal", "medical image deep learning",
            "hearing screening sensor", "bio-signal classification"
        ]
    ),
    (
        "Sensor Fusion & DAQ",
        [
            "inertial sensor fusion", "real-time data acquisition FPGA",
            "multi-sensor orientation tracking", "embedded telemetry systems",
            "sun sensor optical tracking", "IMU state estimation"
        ]
    )
]

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
            requests.post(url, data={"chat_id": TELEGRAM_CHAT_ID, "caption": caption}, files={"document": doc}, timeout=35)
    except Exception as e:
        print(f"[!] Document send error: {e}")

def clean_university_name(name):
    name = str(name).strip()
    if " and " in name:
        name = name.split(" and ")[0]
    if " / " in name:
        name = name.split(" / ")[-1]
    name = re.sub(r'\(.*?\)', '', name)
    name = re.sub(r'[",]', '', name)
    return name.strip()

def search_faculty(raw_uni_name, include_localization=False):
    clean_name = clean_university_name(raw_uni_name)
    mode_str = "ALL FIELDS (Localization + General)" if include_localization else "GENERAL FIELDS ONLY"
    print(f"--> Searching OpenAlex for: {clean_name} [{mode_str}]")
    discovered = []
    headers = {"User-Agent": "mailto:phd_lead_bot@academic.org"}

    try:
        inst_url = f"https://api.openalex.org/institutions?search={requests.utils.quote(clean_name)}"
        res = requests.get(inst_url, headers=headers, timeout=15).json()
        if not res.get("results"):
            print(f"    [!] Institution not resolved for: {clean_name}")
            return discovered
        inst_id = res["results"][0]["id"]
    except Exception as e:
        print(f"    [!] Error looking up institution: {e}")
        return discovered

    active_groups = (LOCALIZATION_GROUPS + GENERAL_GROUPS) if include_localization else GENERAL_GROUPS

    for field_label, kw_list in active_groups:
        query_str = " OR ".join([f'"{k}"' for k in kw_list[:5]])
        works_url = (
            f"https://api.openalex.org/works?"
            f"filter=authorships.institutions.id:{inst_id},from_publication_date:2023-01-01"
            f"&search={requests.utils.quote(query_str)}"
            f"&per-page=2"
        )
        try:
            w_res = requests.get(works_url, headers=headers, timeout=15).json()
            works = w_res.get("results", [])
            for work in works:
                title = work.get("title", "N/A")
                pub_year = work.get("publication_year", "")
                authorships = work.get("authorships", [])

                chosen = authorships[-1] if len(authorships) > 1 else (authorships[0] if authorships else None)
                if chosen:
                    author_info = chosen.get("author", {})
                    discovered.append({
                        "field": field_label,
                        "professor": author_info.get("display_name", "Lab PI"),
                        "university": raw_uni_name,
                        "paper": title,
                        "year": pub_year,
                        "profile_url": author_info.get("id", "")
                    })
            if len(discovered) >= 3:
                break
        except Exception as e:
            print(f"    [!] Error querying works for {field_label}: {e}")

    return discovered

def main():
    print(f"Loading file: {FILE_NAME}")
    if not os.path.exists(FILE_NAME):
        print(f"[!] Error: {FILE_NAME} not found.")
        return

    df = pd.read_csv(FILE_NAME) if FILE_NAME.endswith(".csv") else pd.read_excel(FILE_NAME)

    for col in ["Checked?", "Checked_General?", "Discovered_Field", "General_PI", "General_Paper"]:
        if col not in df.columns:
            df[col] = "NO" if "Checked" in col else ""

    mask = (
        (df["University"].notna()) &
        (df["University"].astype(str).str.strip() != "") &
        (df["University"].astype(str).str.lower() != "nan") &
        (df["Checked_General?"].astype(str).str.upper() != "YES")
    )
    pending_rows = df[mask]

    if pending_rows.empty:
        print("[✓] All universities checked.")
        send_telegram_html("🎉 <b>All universities in your list have been processed!</b>")
        return

    batch = pending_rows.head(5)
    universities = batch["University"].tolist()
    print(f"Processing Batch of 5: {universities}")

    all_matches = []
    for idx, row in batch.iterrows():
        uni = str(row["University"]).strip()
        # If 'Checked?' is NOT 'YES', this is a newly added university -> search Localization + General!
        already_checked_loc = str(row.get("Checked?", "")).strip().upper() == "YES"
        profs = search_faculty(uni, include_localization=(not already_checked_loc))

        df.at[idx, "Checked?"] = "YES"
        df.at[idx, "Checked_General?"] = "YES"

        if profs:
            all_matches.extend(profs)
            df.at[idx, "Discovered_Field"] = profs[0]["field"]
            df.at[idx, "General_PI"] = profs[0]["professor"]
            df.at[idx, "General_Paper"] = f"{profs[0]['paper']} ({profs[0]['year']})"
            df.at[idx, "Note"] = profs[0]["profile_url"]
        time.sleep(1)

    # Save to BOTH .xlsx and .csv so both files stay in sync
    try:
        df.to_excel(XLSX_FILE, index=False)
    except Exception:
        pass
    try:
        df.to_csv(CSV_FILE, index=False)
    except Exception:
        pass

    if all_matches:
        msg = f"🎯 <b>Discovered {len(all_matches)} Labs:</b>\n\n"
        for item in all_matches[:8]:
            msg += (
                f"🏛 <b>{item['university']}</b>\n"
                f"🏷 <b>Field:</b> <code>{item['field']}</code>\n"
                f"👤 <b>PI:</b> {item['professor']}\n"
                f"📄 <b>Recent Work ({item['year']}):</b> <i>{item['paper'][:85]}...</i>\n"
                f"🔗 <a href='{item['profile_url']}'>OpenAlex Author Profile</a>\n\n"
            )
        send_telegram_html(msg)
    else:
        send_telegram_html(
            f"🔍 <b>Checked 5 Universities:</b>\n" +
            "\n".join([f"• {u}" for u in universities]) +
            "\n\n<i>No publications found in this cycle.</i>"
        )

    send_telegram_document(XLSX_FILE, caption="💾 Updated tracking sheet.")

if __name__ == "__main__":
    main()
