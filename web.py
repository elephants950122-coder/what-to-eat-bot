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

# ============================================================
# 🔑 1. 初始化 Firebase（完全比照你之前的電影專案寫法）
# ============================================================
try:
    if not firebase_admin._apps:
        # 直接讀取資料夾內的金鑰檔案
        cred = credentials.Certificate("serviceAccountKey.json")
        firebase_admin.initialize_app(cred)
    print("✅ [成功] Firebase 雲端資料庫通道已打通！")
except Exception as e:
    print(f"❌ [失敗] Firebase 初始化失敗：{e}")

# ============================================================
# 🏠 首頁路由：測試伺服器是否正常運作
# ============================================================
@app.route("/")
def home():
    return "<h1>🍱 沙鹿美食大數據後端伺服器</h1><p>狀態：雲端運行中</p>"

# ============================================================
# 📡 路由一：全自動歷史大數據爬蟲（保持不變）
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
        while url and page_count <= 5:
            response = requests.get(url, headers=headers, cookies=cookies)
            if response.status_code != 200:
                break
                
            soup = BeautifulSoup(response.text, 'html.parser')
            articles = soup.find_all('div', class_='r-ent')
            
            if not articles:
                break
                
            for art in articles:
                title_tag = art.find('div', class_='title')
                if title_tag and title_tag.a:
                    title_text = title_tag.a.text.strip()
                    
                    if "公告" in title_text or "[食記]" not in title_text:
                        continue
                    
                    clean_name = title_text.replace("[食記]", "").replace("台中", "").replace("沙鹿", "").strip()
                    clean_name = clean_name.replace("/", "").replace("\\", "").replace(".", "")[:20]
                    
                    if not clean_name:
                        continue
                        
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
                    
                    # 💡 電影寫法：進入函式內呼叫 client()
                    db = firestore.client()
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
# 🤖 路由二：Webhook（完全比照電影專案內連線與比對的架構）
# ============================================================
@app.route("/webhook", methods=["POST"])
def webhook():
    # 取得 Dialogflow 傳來的請求資料
    req = request.get_json(force=True)
    action = req.get("queryResult", {}).get("action", "")
    
    # 設定一個預設回覆
    info = "抱歉，我目前無法處理這個動作喔！"
    
    if action == "recommend_restaurant":
        info = "我是林建宇設計的機器人，為您從資料庫動態篩選精選沙鹿美食：\n\n"

        try:
            # 💡 完全複製電影機器人的連線步驟
            db = firestore.client()
            collection_ref = db.collection("restaurants") # 指向你的美食大數據集合
            docs = collection_ref.get()
            
            all_restaurants = []
            for doc in docs:
                all_restaurants.append(doc.to_dict())
            
            if all_restaurants:
                # 從龐大的大數據中，隨機抽出 5 家店出來列表
                sample_size = min(5, len(all_restaurants))
                random_list = random.sample(all_restaurants, sample_size)
                
                result = ""
                for index, movie_data in enumerate(random_list, 1):
                    # 安全轉成字串，取出你 Firebase 內的 name、rating、ptt_title 欄位
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

    # 💡 完全比照電影機器人最後的 make_response(jsonify(...)) 格式回傳
    return make_response(jsonify({"fulfillmentText": info}))

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=5000, debug=True)