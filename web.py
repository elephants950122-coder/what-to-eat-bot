import os
import json
import random
import time
import urllib.parse
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
# 🧼 核心優化：強力資料清洗器 (只保留純粹店名)
# ============================================================
def clean_restaurant_name(raw_title):
    # 移除 PTT 常見標籤與地區贅字
    name = raw_title.replace("[食記]", "").replace("台中", "").replace("沙鹿", "")
    
    # 定義要徹底清除的干擾符號清單
    garbage_symbols = ["-", "—", "[", "]", "【", "】", "(", ")", "（", "）", "～", "~", "：", ":", "/", "\\", "、", "."]
    for symbol in garbage_symbols:
        name = name.replace(symbol, "")
        
    # 移除常見的開頭贅詞，精準鎖定店名
    noise_words = ["美食", "下午茶", "景觀餐廳", "夜景很漂亮", "美食推薦", "在地老店", "巷子裡歷史悠久的老店"]
    for word in noise_words:
        name = name.replace(word, "")
        
    # 徹底清除首尾及內部多餘空格，並限制長度
    name = name.strip().replace(" ", "")
    return name[:15]

# ============================================================
# 🏠 1. 首頁路由
# ============================================================
@app.route("/")
def home():
    return render_template("index.html")

# ============================================================
# 📡 2. 爬蟲路由 (內建雲端資料庫覆蓋、去重、強力清洗)
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
    page_count = 1
    
    try:
        safe_init_firebase()
        db = firestore.client()
        
        while url and page_count <= 3:
            response = requests.get(url, headers=headers, cookies=cookies)
            if response.status_code != 200: break
                
            soup = BeautifulSoup(response.text, 'html.parser')
            articles = soup.find_all('div', class_='r-ent')
            if not articles: break
                
            for art in articles:
                title_tag = art.find('div', class_='title')
                if title_tag and title_tag.a:
                    title_text = title_tag.a.text.strip()
                    
                    if "公告" in title_text or "[食記]" not in title_text:
                        continue
                    
                    # 💡 調用強力清洗器，剔除 [ ] - 等所有髒符號
                    clean_name = clean_restaurant_name(title_text)
                    
                    if not clean_name or len(clean_name) <= 1: 
                        continue
                        
                    simulated_rating = round(random.uniform(4.0, 4.9), 1)
                    
                    doc = {
                        "name": clean_name,
                        "ptt_title": title_text,
                        "area": location,
                        "rating": simulated_rating,
                        "type": "美食",
                        "source": "PTT Food板大數據",
                        "sync_time": firestore.SERVER_TIMESTAMP
                    }
                    
                    # 💡 關鍵去重邏輯：檢查 Firebase 裡是不是早就存在這家店了
                    # 用 .where("name", "==", clean_name) 來查詢
                    existing_docs = db.collection("restaurants").where("name", "==", clean_name).get()
                    
                    if len(existing_docs) > 0:
                        # 找到了！代表資料重覆，我們直接「覆蓋更新」第一筆找到的資料
                        doc_id = existing_docs[0].id
                        db.collection("restaurants").document(doc_id).update(doc)
                        # 更新就不算在「全新新增」的筆數裡
                    else:
                        # 沒找到！代表是新店家，直接 add 新增
                        db.collection("restaurants").add(doc)
                        total_inserted += 1
            
            time.sleep(0.3)
            page_count += 1
            
            btn_tags = soup.find_all('a', class_='btn wide')
            url = None
            for btn in btn_tags:
                if "上頁" in btn.text and 'href' in btn.attrs:
                    url = "https://www.ptt.cc" + btn['href']
                    break
                    
        # 撈出不重複的所有美食清單展示
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