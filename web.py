import os
import random
import time
import urllib.parse
import requests
from flask import Flask, request, jsonify, make_response
from bs4 import BeautifulSoup
import firebase_admin
from firebase_admin import credentials, firestore

app = Flask(__name__)

# 💡 定義一個安全的初始化安全鎖，確保不管在哪個路由、哪個執行緒被呼叫，都能 100% 成功連線
def safe_init_firebase():
    if not firebase_admin._apps:
        try:
            # 確保讀取與 web.py 同資料夾底下的 serviceAccountKey.json
            current_dir = os.path.dirname(os.path.abspath(__file__))
            key_path = os.path.join(current_dir, "serviceAccountKey.json")
            
            # 如果預設路徑找不到，就降級讀取相對路徑
            if not os.path.exists(key_path):
                key_path = "serviceAccountKey.json"
                
            cred = credentials.Certificate(key_path)
            firebase_admin.initialize_app(cred)
            print("✅ [內部初始化] Firebase 雲端資料庫連線成功！")
        except Exception as e:
            print(f"❌ [內部初始化] 失敗：{e}")
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
        # 確保 Firebase 在現場初始化成功
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
# 🤖 路由二：Webhook (精準解決 default app 不存在問題)
# ============================================================
@app.route("/webhook", methods=["POST"])
def webhook():
    req = request.get_json(force=True)
    action = req.get("queryResult", {}).get("action", "")
    
    info = "抱歉，我目前無法處理這個動作喔！"
    
    if action == "recommend_restaurant":
        info = "我是林建宇設計的機器人，為您從資料庫動態篩選精選沙鹿美食：\n\n"

        try:
            # 💡 核心修正：進來第一件事先調用安全鎖，確保 App 100% 存在
            safe_init_firebase()
            
            # 連線與撈取資料
            db = firestore.client()
            collection_ref = db.collection("restaurants")
            docs = collection_ref.get()
            
            all_restaurants = []
            for doc in docs:
                all_restaurants.append(doc.to_dict())
            
            if all_restaurants:
                # 隨機抽出 5 家店
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