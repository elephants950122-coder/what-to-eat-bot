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
# 1. 初始化 Firebase
# ============================================================
try:
    if not firebase_admin._apps:
        # 請確保 serviceAccountKey.json 與此檔在同一資料夾
        cred = credentials.Certificate("serviceAccountKey.json")
        firebase_admin.initialize_app(cred)
    db = firestore.client()
    print("✅ [成功] Firebase 已經連線，專案：what-to-eat-bot")
except Exception as e:
    print(f"❌ [失敗] Firebase 初始化失敗: {e}")

# ============================================================
# 🏠 首頁路由：測試伺服器是否正常
# ============================================================
@app.route("/")
def home():
    return "<h1>🍱 沙鹿美食大數據伺服器</h1><p>狀態：運行中</p>"

# ============================================================
# 📡 路由一：全自動爬蟲 (執行此網址會抓 PTT 資料存入 Firebase)
# ============================================================
@app.route("/find_food")
def find_food():
    location = "沙鹿"
    encoded_location = urllib.parse.quote(location)
    
    # PTT Food板搜尋網址
    url = f"https://www.ptt.cc/bbs/Food/search?q={encoded_location}"
    cookies = {'over18': '1'}
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    
    total_inserted = 0
    page_count = 0
    
    try:
        # 最多爬 5 頁，避免過久
        while url and page_count < 5:
            response = requests.get(url, headers=headers, cookies=cookies)
            if response.status_code != 200: break
                
            soup = BeautifulSoup(response.text, 'html.parser')
            articles = soup.find_all('div', class_='r-ent')
            
            if not articles: break
                
            for art in articles:
                title_tag = art.find('div', class_='title')
                if title_tag and title_tag.a:
                    title_text = title_tag.a.text.strip()
                    
                    # 只抓取食記
                    if "[食記]" not in title_text or "公告" in title_text:
                        continue
                    
                    # 清洗店名
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
                        "source": "PTT Food板",
                        "sync_time": firestore.SERVER_TIMESTAMP
                    }
                    
                    # 寫入名為 "restaurants" 的集合
                    db.collection("restaurants").add(doc)
                    total_inserted += 1
            
            time.sleep(0.5)
            page_count += 1
            
            # 找尋上一頁按鈕 (PTT 搜尋結果是倒序的)
            btn_tags = soup.find_all('a', class_='btn wide')
            url = None
            for btn in btn_tags:
                if "上頁" in btn.text and 'href' in btn.attrs:
                    url = "https://www.ptt.cc" + btn['href']
                    break
                    
        return f"✅ 爬取成功！共分析 {page_count} 頁，新增 {total_inserted} 筆資料至 Firebase。"
    except Exception as e:
        return f"❌ 爬蟲發生異常：{e}"

# ============================================================
# 🤖 路由二：Webhook (讓 LINE 機器人真正讀取資料庫)
# ============================================================
@app.route("/webhook", methods=["POST"])
def webhook():
    req = request.get_json(force=True)
    
    # 這裡抓取的是 Dialogflow 中設定的 Action 名稱
    action = req.get("queryResult", {}).get("action", "")
    
    # 終端機列印除錯資訊
    print(f"\n[Incoming Request] Action: {action}")
    
    info = "抱歉，目前沒辦法找到美食資料。"

    # 1. 處理推薦餐廳動作
    if action == "recommend_restaurant":
        print("-> 正在讀取 Firebase 資料...")
        try:
            # 從你的 Firebase "restaurants" 集合抓取資料
            docs = db.collection("restaurants").get()
            all_restaurants = [doc.to_dict() for doc in docs]
            
            print(f"-> 資料庫內共有 {len(all_restaurants)} 筆資料")

            if all_restaurants:
                # 隨機挑選 5 家 (模仿電影機器人的多樣性)
                sample_size = min(5, len(all_restaurants))
                random_list = random.sample(all_restaurants, sample_size)
                
                result_text = "🔎 根據後端資料庫，為您精選沙鹿美食：\n\n"
                for index, item in enumerate(random_list, 1):
                    name = item.get("name", "未知店家")
                    rating = item.get("rating", "4.0")
                    title = item.get("ptt_title", "無標題")
                    
                    result_text += f"🍱 推薦 {index}：{name}\n"
                    result_text += f"⭐ 鄉民評分：{rating}\n"
                    result_text += f"💬 來源：{title}\n\n"
                
                info = result_text + "祝您用餐愉快！😋"
            else:
                info = "📋 目前資料庫內沒資料，請先連線到 /find_food 進行爬取。"
                
        except Exception as e:
            info = "❌ 讀取資料庫時出錯。"
            print(f"❌ [Error] 錯誤原因: {e}")

    # 2. 處理列出所有餐廳清單動作 (如果有設定此 Intent)
    elif action == "GetFoodList":
        try:
            docs = db.collection("restaurants").get()
            titles = [doc.to_dict().get("name") for doc in docs if doc.to_dict().get("name")]
            if titles:
                unique_titles = list(set(titles))
                info = "📋 目前資料庫收錄的沙鹿美食：\n\n- " + "\n- ".join(unique_titles[:30]) # 最多列30個
            else:
                info = "資料庫目前沒有資料。"
        except Exception as e:
            info = f"讀取清單失敗：{e}"

    # 回傳給 Dialogflow (格式必須嚴格遵守)
    response_data = {"fulfillmentText": info}
    res = make_response(jsonify(response_data))
    res.headers['Content-Type'] = 'application/json'
    return res

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=5000, debug=True)