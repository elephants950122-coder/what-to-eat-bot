import os
import json
import random
import time
import urllib.parse
import re
import requests
from flask import Flask, request, jsonify, make_response, render_template
from bs4 import BeautifulSoup
import firebase_admin
from firebase_admin import credentials, firestore

current_dir = os.path.dirname(os.path.abspath(__file__))
app = Flask(__name__, template_folder=os.path.join(current_dir, "templates"))

# 💡 啟用 Flask 內建快閃訊息功能必須設定的金鑰
app.secret_key = "providence_im_secret_key"

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
# 🧼 終極符號與贅字終結者 (資料清洗演算法)
# ============================================================
def super_clean_title(raw_title):
    if not raw_title:
        return ""
        
    name = raw_title.upper()
    name = name.replace("[食記]", "").replace("食記", "")\
               .replace("台中市", "").replace("台中", "")\
               .replace("沙鹿區", "").replace("沙鹿", "")\
               .replace("FW:", "").replace("FW", "")\
               .replace("630前買1送1", "")
    
    # 強力過濾：只保留 中文字、英文字母、數字
    name = re.sub(r'[^\u4e00-\u9fa5A-Z0-9]', '', name)
    
    front_garbage = ["區", "市", "鎮", "鄉"]
    while len(name) > 0 and name[0] in front_garbage:
        name = name[1:]
        
    return name.strip()[:20]

# ============================================================
# 🏠 1. 首頁管理後台路由
# ============================================================
@app.route("/")
def home():
    return render_template("index.html")

# ============================================================
# 🤖 2. 網頁免登入對話測試端點
# ============================================================
@app.route("/chat")
def chat_page():
    return render_template("webdamo.html")

# ============================================================
# 📡 3. 深度無限翻頁爬蟲 (利用店名當 Doc ID 機制做到天生去重)
# ============================================================
@app.route("/find_food")
def find_food():
    location = "沙鹿"
    encoded_location = urllib.parse.quote(location)
    
    # 初始搜尋網址
    url = f"https://www.ptt.cc/bbs/Food/search?q={encoded_location}"
    cookies = {'over18': '1'}
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    
    total_inserted = 0
    page_count = 0
    
    try:
        safe_init_firebase()
        db = firestore.client()
        
        # 💡 只要有上頁按鈕就永無止境爬下去
        while url:
            response = requests.get(url, headers=headers, cookies=cookies)
            if response.status_code != 200:
                break
                
            soup = BeautifulSoup(response.text, 'html.parser')
            articles = soup.find_all('div', class_='r-ent')
            
            if not articles:
                break
                
            page_count += 1
            
            for art in articles:
                title_tag = art.find('div', class_='title')
                if title_tag and title_tag.a:
                    title_text = title_tag.a.text.strip()
                    
                    if "公告" in title_text or "[食記]" not in title_text:
                        continue
                    
                    clean_name = super_clean_title(title_text)
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
                    
                    # 💡 用 clean_name 當 Document ID，多點幾次也絕對不會產生重複的垃圾資料！
                    db.collection("restaurants").document(clean_name).set(doc)
                    total_inserted += 1
            
            # 💡 翻頁演算法：自動從 HTML 提取 PTT 的「‹ 上頁」按鈕
            btn_tags = soup.find_all('a', class_='btn wide')
            url = None 
            for btn in btn_tags:
                if "上頁" in btn.text and 'href' in btn.attrs:
                    url = "https://www.ptt.cc" + btn['href']
                    break
            
            time.sleep(0.3)
                        
        docs = db.collection("restaurants").order_by("sync_time", direction=firestore.Query.DESCENDING).get()
        restaurant_list = [doc.to_dict() for doc in docs]
        total_in_db = len(restaurant_list)
            
        return render_template("result.html", total_inserted=total_inserted, total_in_db=total_in_db, restaurants=restaurant_list)
        
    except Exception as e:
        return f"❌ 爬蟲中斷（可能因數據過大超時），目前已同步：{total_inserted} 筆。原因：{e}"

# ============================================================
# 🗑️ 4. 資料庫優化管理：一鍵清空資料庫 (利用 Flash 回傳首頁)
# ============================================================
@app.route("/delete_all")
def delete_all():
    try:
        safe_init_firebase()
        db = firestore.client()
        docs = db.collection("restaurants").list_documents()
        count = 0
        for doc in docs:
            doc.delete()
            count += 1
            
        # 💡 核心機制：透過 flash 將成功訊息塞入 Session，並重新導向首頁
        flash(f"報告管理員！已成功連線 Firebase 雲端資料庫並清空共 {count} 筆歷史垃圾快取！數據已重置為全新狀態。", "success")
        return redirect(url_for('home'))
    except Exception as e:
        flash(f"❌ 系統清空資料庫失敗，錯誤原因: {e}", "danger")
        return redirect(url_for('home'))

# ============================================================
# 🤖 5. Webhook 通道 (LINE 機器人核心對接組件)
# ============================================================
@app.route("/webhook", methods=["POST"])
def webhook():
    req = request.get_json(force=True)
    query_result = req.get("queryResult", {})
    action = query_result.get("action", "")
    parameters = query_result.get("parameters", {})
    
    raw_location = parameters.get("location", "沙鹿")
    if isinstance(raw_location, dict):
        loc_str = raw_location.get("subadmin-area") or raw_location.get("city") or raw_location.get("admin-area") or "沙鹿"
        user_location = loc_str.replace("區", "").replace("市", "").strip()
    else:
        user_location = str(raw_location).replace("區", "").replace("市", "").strip()
        
    if not user_location:
        user_location = "沙鹿"

    user_food_type = parameters.get("food_type", "") 
    info = "抱歉，我目前無法處理這個動作喔！"
    
    if action == "recommend_restaurant":
        try:
            safe_init_firebase()
            db = firestore.client()
            docs = db.collection("restaurants").get()
            all_restaurants = [doc.to_dict() for doc in docs]
            
            if all_restaurants:
                filtered_list = [r for r in all_restaurants if r.get("area") == user_location]
                
                type_keywords = {
                    "宵夜": ["宵夜", "宵夜", "深夜", "燒烤", "串燒", "酒吧", "永和豆漿"],
                    "下午茶": ["下午茶", "點心", "蛋糕", "甜點", "咖啡", "冰品", "豆花", "手搖", "麵包", "烘焙"],
                    "早午餐": ["早午餐", "早餐", "BRUNCH", "蛋餅", "吐司", "漢堡", "飯糰"]
                }
                
                if user_food_type and user_food_type in type_keywords:
                    keywords = type_keywords[user_food_type]
                    category_matched_list = []
                    for r in filtered_list:
                        title_upper = r.get("ptt_title", "").upper()
                        if any(kw in title_upper for kw in keywords):
                            category_matched_list.append(r)
                            
                    filtered_list = category_matched_list
                    info = f"🤖 已為您連線 Firebase，從小組專屬大數據庫中精選出符合【{user_location} {user_food_type}】的口袋名單：\n\n"
                else:
                    info = f"🤖 已為您從 Firebase 大數據中，隨機精選 5 間【{user_location}】在地好料：\n\n"
                
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
                    info = f"📋 報告！目前 Firebase 大數據庫中，暫時還沒有關於【{user_location} {user_food_type}】的精確食記。"
            else:
                info = "📋 目前資料庫內暫無美食資料，請先前往管理後端進行網頁爬取同步！"
        except Exception as e:
            info = f"❌ 後端執行錯誤，原因: {str(e)}"
            
    return make_response(jsonify({"fulfillmentText": info}))

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=5000, debug=True)