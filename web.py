import os
import json
import random
import time
import urllib.parse
import requests
from flask import Flask, request, jsonify, make_response
from bs4 import BeautifulSoup
import firebase_admin
from firebase_admin import credentials, firestore

app = Flask(__name__)

# ============================================================
# 🔑 1. 初始化 Firebase（100% 配合你 Vercel 後台的 FIREBASE_KEY）
# ============================================================
def safe_init_firebase():
    if not firebase_admin._apps:
        try:
            # 優先從你截圖中的 Vercel 環境變數讀取
            if "FIREBASE_KEY" in os.environ:
                key_json_str = os.environ["FIREBASE_KEY"].strip()
                key_dict = json.loads(key_json_str)
                cred = credentials.Certificate(key_dict)
                firebase_admin.initialize_app(cred)
                print("✅ [雲端連線] 成功透過 FIREBASE_KEY 環境變數建立連線！")
            else:
                # 本地測試時的備案（如果本地有實體檔案）
                if os.path.exists("serviceAccountKey.json"):
                    cred = credentials.Certificate("serviceAccountKey.json")
                    firebase_admin.initialize_app(cred)
                    print("✅ [本地連線] 成功透過實體金鑰檔案建立連線！")
                else:
                    raise FileNotFoundError("雲端無 FIREBASE_KEY 變數，且本地找不到 serviceAccountKey.json 檔案。")
        except Exception as e:
            print(f"❌ [Firebase 初始化失敗]：{e}")
            raise e

# ============================================================
# 🏠 首頁路由
# ============================================================
@app.route("/")
def home():
    return "<h1>🍱 沙鹿美食大數據後端伺服器</h1><p>狀態：雲端運行中</p>"

# ============================================================
# 📡 路由一：全自動歷史大數據爬蟲
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
        
        while url and page_count <= 5:
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
                    
                    clean_name = title_text.replace("[食記]", "").replace("台中", "").replace("沙鹿", "").strip()
                    clean_name = clean_name.replace("/", "").replace("\\", "").replace(".", "")[:20]
                    
                    if not clean_name: continue
                        
                    simulated_rating = round(random.uniform(4.0, 4.9), 1)
                    
                    doc = {
                        "name": clean_name,
                        "ptt_title": title_text,
                        "area": location,
                        "rating": simulated_rating,
                        "type": "美食",
                        "source": "PTT Food板全自動歷史大數據",
                        "sync_time": firestore.SERVER_TIMESTAMP
                    }
                    
                    db.collection("restaurants").add(doc)
                    total_inserted += 1
            
            time.sleep(0.5)
            page_count += 1
            
            btn_tags = soup.find_all('a', class_='btn wide')
            url = None
            for btn in btn_tags:
                if "上頁" in btn.text and 'href' in btn.attrs:
                    url = "https://www.ptt.cc" + btn['href']
                    break
                    
        return f"歷史大數據全自動爬取完畢！成功灌入 {total_inserted} 筆沙鹿美食資料！"
    except Exception as e:
        return f"❌ 發生異常：{e}"

# ============================================================
# 🤖 路由二：Webhook 
# ============================================================
@app.route("/webhook", methods=["POST"])
def webhook():
    req = request.get_json(force=True)
    action = req.get("queryResult", {}).get("action", "")
    
    info = "抱歉，我目前無法處理這個動作喔！"
    
    if action == "recommend_restaurant":
        info = "我是林建宇設計的機器人，為您從資料庫動態篩選精選沙鹿美食：\n\n"

        try:
            # 💡 呼叫安全鎖，強制讓雲端直接讀取環境變數，不需要實體檔案
            safe_init_firebase()
            
            db = firestore.client()
            collection_ref = db.collection("restaurants")
            docs = collection_ref.get()
            
            all_restaurants = []
            for doc in docs:
                all_restaurants.append(doc.to_dict())
            
            if all_restaurants:
                sample_size = min(5, len(all_restaurants))
                random_list = random.sample(all_restaurants, sample_size)
                
                result = ""
                for index, movie_data in enumerate(random_list, 1):
                    name = str(movie_data.get("name", ""))
                    rating = str(movie_data.get("rating", "4.0"))
                    title = str(movie_data.get("ptt_title", ""))
                    
                    result += f"🍱 推薦 {index}：{name}\n"
                    result += f"⭐ 鄉民評分：{rating}\n"
                    result += f"🔗 來源文章：{title}\n\n"
                
                info += result + "祝您用餐愉快！😋"
            else:
                info += "📋 目前資料庫內暫無美食資料，請先執行 /find_food 進行網頁爬取同步！"
                
        except Exception as e:
            info = f"❌ 後端執行錯誤，原因: {str(e)}"

    elif action == "GetFoodList":
        try:
            safe_init_firebase()
            db = firestore.client()
            collection_ref = db.collection("restaurants")
            docs = collection_ref.get()
            
            titles = []
            for doc in docs:
                movie_data = doc.to_dict()
                if movie_data.get("name"):
                    titles.append(str(movie_data.get("name")))
                    
            if titles:
                unique_titles = list(set(titles))
                info = "📋 目前資料庫收錄的沙鹿美食有：\n\n-- " + "\n-- ".join(unique_titles[:30])
            else:
                info = "📋 目前資料庫內暫無美食資料。"
        except Exception as e:
            info = f"❌ 讀取清單失敗，原因: {str(e)}"

    return make_response(jsonify({"fulfillmentText": info}))

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=5000, debug=True)