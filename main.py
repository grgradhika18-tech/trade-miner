import os
import requests
import feedparser
import yfinance as yf
import pycountry
import json
import re
from rapidfuzz import process
from neo4j import GraphDatabase
from groq import Groq
from newspaper import Article
from urllib.parse import urlparse
from datetime import datetime

# --- 1. CONFIGURATION ---
GROQ_API_KEY = os.getenv("GROQ_API_KEY") 
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD") 
NEO4J_USER = "neo4j"
NEO4J_URI = os.getenv("NEO4J_URI")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

if not GROQ_API_KEY or not NEO4J_PASSWORD or not NEO4J_URI:
    print("❌ Keys Missing.")
    exit()

driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
client = Groq(api_key=GROQ_API_KEY)

# --- 2. KNOWLEDGE BASE ---
PVC_KNOWLEDGE_BASE = {
    "GRADES": ["K-67", "K-57", "Suspension", "Paste", "Emulsion"],
    "DUTIES": ["Anti-Dumping", "Basic Customs Duty", "CVD"],
    "LOGISTICS": ["Container", "Freight", "FOB", "CFR", "CIF"]
}

# --- 3. PHYSICS ENGINE ---
class TradePhysicsEngine:
    def __init__(self):
        self.pressure = {"China": 95, "India": 20, "Vietnam": 60, "USA": 50}
        self.specific_heat = {"China": 0.8, "India": 0.3, "Vietnam": 0.2, "USA": 2.0}
        self.temp = {"China": 80, "India": 110, "Vietnam": 100}
        self.coriolis = {("China", "India"): 0.5}

    def update_physics(self, country, event_type, severity):
        if "Factory" in event_type or "Plant" in event_type:
            self.pressure[country] = self.pressure.get(country, 50) - severity
        if country in self.specific_heat:
            cp = self.specific_heat[country]
            delta_t = severity / cp 
            self.temp[country] += delta_t
            biome = "Temperate"
            if self.temp[country] > 125: biome = "🔥 DESERT (Panic)"
            if self.temp[country] < 55:  biome = "❄️ GLACIER (Frozen)"
            return biome
        return "Normal"

    def update_friction(self, source, target, is_blocked):
        val = 0.95 if is_blocked else 0.1
        self.coriolis[(source, target)] = val

physics_engine = TradePhysicsEngine()

# --- 4. ALERTS ---
def send_telegram_alert(message):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID: return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"}
    try: requests.post(url, json=payload, timeout=5)
    except: pass

# --- 5. SOURCES ---
def get_source_trust_score(url):
    domain = urlparse(url).netloc.lower()
    god_tier = ["reuters.com", "bloomberg.com", "icis.com", "chemanalyst.com", "dgft.gov.in"]
    return 100 if any(t in domain for t in god_tier) else 50

# --- 6. INTELLIGENCE FETCH ---
def fetch_intelligence():
    print("🛡️ Polymer Sentinel: Scanning...")
    articles = []
    # Query targeted at PRICES and BANS
    query = '(PVC OR "Polyvinyl Chloride") (Price OR "Anti-dumping" OR BIS OR Ban OR Suspended) (India OR Import) -forecast -report'
    url = f"https://api.gdeltproject.org/api/v2/doc/doc?query={query} sourcelang:eng&mode=artlist&maxrecords=40&format=json&sort=DateDesc"
    try:
        data = requests.get(url, timeout=10).json()
        if 'articles' in data:
            for item in data['articles']:
                try:
                    art = Article(item['url'])
                    art.download(); art.parse()
                    articles.append({
                        "text": art.text[:2000], "url": item['url'], 
                        "title": item['title'], "domain": urlparse(item['url']).netloc,
                        "base_trust": get_source_trust_score(item['url']), "type": "NEWS"
                    })
                except: pass
    except: pass
    return articles

# --- 7. STRICT JSON ANALYST ---
def analyze_and_process(article):
    # WE FORCE JSON OUTPUT ONLY. NO TALKING.
    prompt = f"""
    Analyze this PVC Trade news. Output ONLY valid JSON.
    
    NEWS: {article['title']}
    TEXT: {article['text'][:500]}...
    
    EXTRACT:
    1. exporters: List of strings (e.g. ["China", "Taiwan"]). If global, use ["Global"].
    2. product: String (e.g. "PVC Resin").
    3. status: String (e.g. "Price Hike", "BIS Rejected", "Anti-Dumping Imposed").
    4. price: Number or null (Extract $/MT or Rs/kg if found).
    5. severity: Integer 1-10.
    6. confidence: Integer 0-100.
    7. tech_params: String listing any specs (e.g. "K-67, Viscosity").
    
    JSON FORMAT:
    {{
        "exporters": ["China", "Korea"],
        "product": "PVC",
        "status": "Price Hike",
        "price": 850,
        "severity": 8,
        "confidence": 90,
        "tech_params": "None"
    }}
    """
    
    try:
        completion = client.chat.completions.create(
            messages=[{"role": "user", "content": prompt}],
            model="deepseek-r1-distill-llama-70b"
        )
        
        content = completion.choices[0].message.content
        # Remove DeepSeek <think> tags if present
        if "</think>" in content: content = content.split("</think>")[-1]
        
        # FIND JSON block (Regex)
        json_match = re.search(r'\{.*\}', content, re.DOTALL)
        if not json_match: return
        
        data = json.loads(json_match.group())
        
        # Process EACH Exporter separately (Split "China, Taiwan" into 2 rows)
        for exporter in data.get("exporters", ["Unknown"]):
            exporter = exporter.strip()
            if len(exporter) < 2: continue # Skip empty
            
            print(f"   🧠 Data Extracted: {exporter} -> {data['status']} (${data['price']})")
            
            # Physics
            biome = physics_engine.update_physics(exporter, data['status'], data['severity'])
            if "Ban" in data['status']: physics_engine.update_friction(exporter, "India", True)

            # Save to Neo4j
            query = """
            MERGE (e:Exporter {name: $exp})
            MERGE (i:Market {name: 'India'})
            MERGE (e)-[r:BIS_STATUS]->(i)
            SET r.status = $stat, r.price_point = $price, r.confidence = $conf,
                r.technical_params = $tech, r.biome = $biome, r.sources = [$url],
                r.last_updated = datetime()
            """
            with driver.session() as session:
                session.run(query, exp=exporter, stat=data['status'], price=str(data['price']),
                            conf=data['confidence'], tech=data['tech_params'], 
                            url=article['url'], biome=biome)
            
            # Alert
            if data['severity'] >= 8:
                send_telegram_alert(f"🚨 **{exporter} ALERT**: {data['status']} (${data['price']})")

    except Exception as e:
        pass

# --- 8. RUNNER ---
if __name__ == "__main__":
    intelligence = fetch_intelligence()
    for data in intelligence:
        analyze_and_process(data)
    driver.close()
