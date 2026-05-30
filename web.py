import os
import json
import random
import time
import urllib.parse
import re  # 💡 引入正規表達式模組
import requests
from flask import Flask, request, jsonify, make_response, render_template
from bs4 import BeautifulSoup
import firebase_admin
from firebase_admin import credentials, firestore

current_dir = os.path.dirname(os.path.abspath(__file__))
app = Flask(__name__, template_folder=os.path.join(current_dir, "templates"))

def safe_init_firebase():
    if not firebase_admin._apps:
        try:
            if "FIREBASE_KEY" in os.environ:
                key_json_str = os.environ["FIREBASE_KEY"].strip()
                key_dict = json.loads(key_json_str)
                cred = credentials.Certificate(key_dict)
                firebase_admin.initialize_app(cred)
            else:
                local_key = os.path.join(current_dir, "serviceAccountKey.json")
                if os.path.exists(local_key):
                    cred = credentials.Certificate(local_key)
                    firebase_admin.initialize_app(cred)
                else:
                    raise FileNotFoundError("找不到 Firebase 金鑰")
        except Exception as e:
            print(f"❌ [Firebase 初始化失敗]：{e}")
            raise e

# ============================================================
# 🧼 終極符號終結者：只允許中英數，所有特殊符號、引號、箭頭強制蒸發
# ============================================================
def super_clean_title(raw_title):
    # 1. 先把明顯的贅字全面剔除（不管它有沒有帶括號）
    name = raw_title.replace("食記", "").replace("台中", "").replace("沙鹿", "")
    
    # 2. 💡 核心大絕招：利用正規表達式，只保留 中文字 (\u4e00-\u9fa5)、英文字母 (a-zA-Z)、數字 (0-9)
    # 所有引號 ""、「」、、橫槓 -、特殊箭頭 →、空格，只要不在白名單內的通通直接變不見！
    name = re.sub(r'[^\u4e00-\u9fa5a-zA-Z0-9]', '', name)
    
    return name.strip()

# ============================================================
# 🏠 1. 首頁路由
# ============================================================
@app.route("/")
def home():
    return render_template("index.html")

# ============================================================
# 📡 2. 爬蟲路由 (符號終結 + 硬核去重)
# ============================================================
@app.route("/find_food")
def find_food():
    location = "沙鹿"
    encoded_location = urllib.parse.quote(location)
    url = f"https://www.ptt.cc/bbs/Food/search?q={encoded_location}"
    cookies = {'over18': '1'}
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    
    total_inserted = 0
    
    try:
        safe_init_firebase()
        db = firestore.client()
        
        # 撈出目前資料庫裡所有資料，建立比對字典
        existing_docs = db.collection("restaurants").get()
        existing_titles = {}
        for doc in existing_docs:
            data = doc.to_dict()
            if data.get("ptt_title"):
                existing_titles[data.get("ptt_title")] = doc.id
        
        response = requests.get(url, headers=headers, cookies=cookies)
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, 'html.parser')
            articles = soup.find_all('div', class_='r-ent')
            
            for art in articles:
                title_tag = art.find('div', class_='title')
                if title_tag and title_tag.a:
                    title_text = title_tag.a.text.strip()
                    
                    if "公告" in title_text or "[食記]" not in title_text:
                        continue
                    
                    # 💡 呼叫終極符號終結者
                    display_name = super_clean_title(title_text)
                    
                    if not display_name:
                        continue
                    
                    simulated_rating = round(random.uniform(4.0, 4.9), 1)
                    
                    doc = {
                        "name": display_name,         # 絕對純淨的中英數名稱
                        "ptt_title": title_text,       # 原始 PTT 標題（作為唯一比對鍵）
                        "area": location,
                        "rating": simulated_rating,
                        "type": "美食",
                        "source": "PTT Food板大數據",
                        "sync_time": firestore.SERVER_TIMESTAMP
                    }
                    
                    # 精準去重
                    if title_text in existing_titles:
                        dup_id = existing_titles[title_text]
                        db.collection("restaurants").document(dup_id).update(doc)
                    else:
                        db.collection("restaurants").add(doc)
                        total_inserted += 1
                        
        # 重新撈出最乾淨的結果清單
        docs = db.collection("restaurants").order_by("sync_time", direction=firestore.Query.DESCENDING).get()
        restaurant_list = [doc.to_dict() for doc in docs]
        total_in_db = len(restaurant_list)
            
        return render_template("result.html", total_inserted=total_inserted, total_in_db=total_in_db, restaurants=restaurant_list)
        
    except Exception as e:
        return f"❌ 系統發生異常：{e}"

# ============================================================
# 🤖 3. Webhook 通道
# ============================================================
@app.route("/webhook", methods=["POST"])
def webhook():
    req = request.get_json(force=True)
    action = req.get("queryResult", {}).get("action", "")
    info = "抱歉，我目前無法處理這個動作喔！"
    
    if action == "recommend_restaurant":
        info = "我是 What-to-eat-bot，為您從資料庫動態篩選精選沙鹿美食：\n\n"
        try:
            safe_init_firebase()
            db = firestore.client()
            docs = db.collection("restaurants").get()
            all_restaurants = [doc.to_dict() for doc in docs]
            
            if all_restaurants:
                sample_size = min(5, len(all_restaurants))
                random_list = random.sample(all_restaurants, sample_size)
                result = ""
                for index, item_data in enumerate(random_list, 1):
                    name = str(item_data.get("name", "未知店家"))
                    rating = str(item_data.get("rating", "4.0"))
                    title = str(item_data.get("ptt_title", "無來源標題"))
                    result += f"🍱 推薦 {index}：{name}\n⭐ 鄉民評分：{rating}\n🔗 來源文章：{title}\n\n"
                info += result + "祝您用餐愉快！😋"
            else:
                info += "📋 目前資料庫內暫無美食資料，請先前往管理後端進行網頁爬取同步！"
        except Exception as e:
            info = f"❌ 後端執行錯誤，原因: {str(e)}"
            
    return make_response(jsonify({"fulfillmentText": info}))

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=5000, debug=True)