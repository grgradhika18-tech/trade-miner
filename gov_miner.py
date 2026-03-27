import feedparser
import requests
import json
import datetime
import os
from neo4j import GraphDatabase

# --- CONFIGURATION ---
NEO4J_URI = os.getenv("NEO4J_URI")
NEO4J_USER = "neo4j"
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD")

if not NEO4J_URI:
    print("❌ Error: Set NEO4J_URI environment variable.")
    exit()

driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))

# --- SOURCE 1: DGFT (Directorate General of Foreign Trade) ---
# They publish RSS feeds for all new trade notifications.
def fetch_dgft_notifications():
    print("🏛️ Checking DGFT for Trade Bans/Rules...")
    rss_url = "https://www.dgft.gov.in/CP/?format=feed&type=rss" # Standard DGFT Feed
    
    feed = feedparser.parse(rss_url)
    
    for entry in feed.entries:
        title = entry.title
        link = entry.link
        date = entry.published
        
        # FILTER: We only care about PVC/Plastics or Anti-dumping
        keywords = ["PVC", "Plastic", "Polymer", "Anti-dumping", "Import Policy"]
        if any(x in title for x in keywords):
            print(f"   🚨 OFFICIAL DGFT NOTICE: {title}")
            save_gov_alert("DGFT", title, link, "High Priority")

# --- SOURCE 2: WTO (World Trade Organization) ---
# We check for TBT (Technical Barriers to Trade) involving India + Chemicals
def fetch_wto_alerts():
    print("🌐 Checking WTO for Global Trade Barriers...")
    # This is a specialized API call to the WTO ePing system
    # We look for notifications FROM India regarding 'Chemicals'
    api_url = "https://api.epingalert.org/api/v1/notifications/search?notifying_member=IND&products=Chemicals"
    
    try:
        response = requests.get(api_url, timeout=10)
        if response.status_code == 200:
            data = response.json()
            for item in data.get('results', [])[:5]:
                title = item.get('title_en', 'No Title')
                symbol = item.get('symbol', '')
                if "PVC" in title or "Plastic" in title or "Quality Control" in title:
                    print(f"   ⚠️ WTO WARNING ({symbol}): {title}")
                    link = f"https://epingalert.org/en/Search?viewData={symbol}"
                    save_gov_alert("WTO", title, link, "Early Warning")
    except:
        print("   (WTO connection skipped - API might be busy)")

# --- SOURCE 3: BIS & PIB (Via Targeted GDELT Domain Search) ---
# Since BIS doesn't have an easy RSS, we use GDELT but restrict it strictly to .gov.in
def fetch_gov_press_releases():
    print("🇮🇳 Checking Govt Press Releases (PIB/BIS)...")
    # Query: PVC + domain:gov.in (Strict Government Only)
    query = '(PVC OR "Polyvinyl Chloride" OR BIS OR QCO) domain:gov.in'
    url = f"https://api.gdeltproject.org/api/v2/doc/doc?query={query}&mode=artlist&maxrecords=10&format=json&sort=DateDesc"
    
    try:
        data = requests.get(url).json()
        if 'articles' in data:
            for item in data['articles']:
                print(f"   🇮🇳 GOVT RELEASE: {item['title']}")
                save_gov_alert("Govt_India", item['title'], item['url'], "Official")
    except: pass

# --- SAVE TO GRAPH ---
def save_gov_alert(source, title, url, level):
    query = """
    MERGE (g:GovSource {name: $source})
    MERGE (a:Alert {title: $title})
    SET a.url = $url, a.level = $level, a.date = datetime()
    MERGE (g)-[:ISSUED]->(a)
    
    // Connect to India Market
    MERGE (i:Market {name: 'India'})
    MERGE (a)-[:AFFECTS]->(i)
    """
    with driver.session() as session:
        session.run(query, source=source, title=title, url=url, level=level)

# --- RUNNER ---
if __name__ == "__main__":
    fetch_dgft_notifications()
    fetch_wto_alerts()
    fetch_gov_press_releases()
    driver.close()
