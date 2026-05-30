import os
import json
import random
import time
import urllib.parse
import re  # 正規表達式大殺器
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
# 🧼 終極符號與贅字終結者：徹底拔除所有不乾淨的字元
# ============================================================
def super_clean_title(raw_title):
    if not raw_title:
        return ""
        
    # 1. 轉成大寫，這樣不論是 fw 還是 FW 都能精準刪除
    name = raw_title.upper()
    
    # 2. 先把明顯的贅字集合全面剔除
    name = name.replace("[食記]", "").replace("食記", "")\
               .replace("台中市", "").replace("台中", "")\
               .replace("沙鹿區", "").replace("沙鹿", "")\
               .replace("FW:", "").replace("FW", "")\
               .replace("630前買1送1", "")
    
    # 3. 💡 核心大絕招：利用正規表達式，只保留 中文字、英文字母、數字
    # 所有「．」、句號「。」、逗號「，」、引號、空格通通在這一行直接人間蒸發！
    name = re.sub(r'[^\u4e00-\u9fa5A-Z0-9]', '', name)
    
    # 4. 如果開頭不幸還是殘留了地名贅字，用迴圈強制切除開頭
    front_garbage = ["區", "市", "鎮", "鄉"]
    while len(name) > 0 and name[0] in front_garbage:
        name = name[1:]
        
    return name.strip()

# ============================================================
# 🏠 1. 首頁路由
# ============================================================
@app.route("/")
def home():
    return render_template("index.html")

# ============================================================
# 📡 2. 爬蟲路由 (全新架構：使用乾淨店名作為唯一 Document ID)
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
                    
                    # 💡 呼叫終極過濾，此時 display_name 只會剩下純中英數（例如：鮮肉湯包搬家了）
                    display_name = super_clean_title(title_text)
                    
                    if not display_name or len(display_name) <= 1: 
                        continue
                    
                    simulated_rating = round(random.uniform(4.0, 4.9), 1)
                    
                    doc = {
                        "name": display_name,
                        "ptt_title": title_text,
                        "area": location,
                        "rating": simulated_rating,
                        "type": "美食",
                        "source": "PTT Food板大數據",
                        "sync_time": firestore.SERVER_TIMESTAMP
                    }
                    
                    # 💡 【核心重構】直接用「純淨店名」當作 Document ID
                    # 如果該店名已存在，.set(doc) 會直接 100% 強制覆蓋舊資料！絕不重複！
                    db.collection("restaurants").document(display_name).set(doc)
                    total_inserted += 1
                        
        # 撈出最乾淨的結果清單
        docs = db.collection("restaurants").order_by("sync_time", direction=firestore.Query.DESCENDING).get()
        restaurant_list = [doc.to_dict() for doc in docs]
        total_in_db = len(restaurant_list)
            
        return render_template("result.html", total_inserted=total_inserted, total_in_db=total_in_db, restaurants=restaurant_list)
        
    except Exception as e:
        return f"❌ 系統發生異常：{e}"

# ============================================================
# 🤖 3. Webhook 通道
# ============================================================
# ============================================================
# 🤖 Webhook 通道 (智慧聯想分類防翻車版)
# ============================================================
@app.route("/webhook", methods=["POST"])
def webhook():
    req = request.get_json(force=True)
    query_result = req.get("queryResult", {})
    action = query_result.get("action", "")
    
    parameters = query_result.get("parameters", {})
    user_location = parameters.get("location", "沙鹿")
    user_food_type = parameters.get("food_type", "") 
    
    info = "抱歉，我目前無法處理這個動作喔！"
    
    if action == "recommend_restaurant":
        try:
            safe_init_firebase()
            db = firestore.client()
            docs = db.collection("restaurants").get()
            all_restaurants = [doc.to_dict() for doc in docs]
            
            if all_restaurants:
                # 第一層：精準鎖定地區 (如：沙鹿)
                filtered_list = [r for r in all_restaurants if r.get("area") == user_location]
                
                # 第二層：定義智慧分類代號對應的關鍵字白名單
                # 這樣就算 PTT 標題沒寫「下午茶」，只要有「蛋糕」或「甜點」也 100% 會被歸類過來！
                type_keywords = {
                    "宵夜": ["宵夜", "宵夜", "深夜", "燒烤", "串燒", "酒吧", "永和豆漿"],
                    "下午茶": ["下午茶", "點心", "蛋糕", "甜點", "咖啡", "冰品", "豆花", "手搖", "麵包", "烘焙"],
                    "早午餐": ["早午餐", "早餐", "BRUNCH", "蛋餅", "吐司", "漢堡", "飯糰"]
                }
                
                # 如果使用者有指定分類，啟動白名單模糊搜尋機制
                if user_food_type and user_food_type in type_keywords:
                    keywords = type_keywords[user_food_type]
                    
                    # 檢查這家餐廳的原始 PTT 標題有沒有命中任何一個分類關鍵字
                    category_matched_list = []
                    for r in filtered_list:
                        title_upper = r.get("ptt_title", "").upper()
                        # 只要標題裡包含白名單中的任一個詞，就納入推薦
                        if any(kw in title_upper for kw in keywords):
                            category_matched_list.append(r)
                            
                    filtered_list = category_matched_list
                    info = f"🤖 這是建宇的美食機器人！已為您連線 Firebase，從小組專屬大數據庫中精選出符合【{user_location} {user_food_type}】的口袋名單：\n\n"
                else:
                    # 如果使用者沒講種類，或者講了我們沒定義的種類，就維持一般的隨機美味推薦
                    info = f"🤖 這是建宇的美食機器人！已為您從 Firebase 大數據中，隨機精選 5 間【{user_location}】在地好料：\n\n"
                
                # 抽取最多 5 筆回傳
                if filtered_list:
                    sample_size = min(5, len(filtered_list))
                    random_list = random.sample(filtered_list, sample_size)
                    
                    result = ""
                    for index, item_data in enumerate(random_list, 1):
                        name = str(item_data.get("name", "未知店家"))
                        rating = str(item_data.get("rating", "4.0"))
                        title = str(item_data.get("ptt_title", "無來源標題"))
                        result += f"🍱 推薦 {index}：{name}\n⭐ 鄉民評分：{rating}\n🔗 來源文章：{title}\n\n"
                    
                    info += result + "祝您用餐愉快！😋"
                else:
                    info = f"📋 報告！目前建宇的 Firebase 大數據庫中，暫時還沒有關於【{user_location} {user_food_type}】的精確食記。我會提醒建宇趕快去後台點擊爬蟲更新！"
            else:
                info = "📋 目前資料庫內暫無美食資料，請先前往管理後端進行網頁爬取同步！"
        except Exception as e:
            info = f"❌ 後端執行錯誤，原因: {str(e)}"
            
    return make_response(jsonify({"fulfillmentText": info}))