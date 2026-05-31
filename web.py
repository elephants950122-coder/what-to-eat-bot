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
# 🧼 強力清洗演算法：移除形容詞與贅字，只留下「品牌核心」
# ============================================================
def super_clean_title(raw_title):
    if not raw_title:
        return ""
        
    name = raw_title.upper()
    
    # 1. 移除 PTT 標籤與地區贅字
    garbage_list = [
        "[食記]", "食記", "台中市", "台中", "沙鹿區", "沙鹿", "FW:", "FW", "推薦", 
        "必吃", "好吃", "超強", "終於吃到", "隱藏版", "排隊", "平價", "美味", "老店", "大推"
    ]
    for garbage in garbage_list:
        name = name.replace(garbage, "")
    
    # 2. 只保留 中文字、英文字母、數字
    name = re.sub(r'[^\u4e00-\u9fa5A-Z0-9]', '', name)
    
    # 3. 移除頭部的行政區殘留
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
# 🤖 2. 網頁免登入對話測試端點
# ============================================================
@app.route("/chat")
def chat_page():
    return render_template("webdamo.html")

# ============================================================
# 📡 3. 智慧去重爬蟲：包含關係比對法 (Fuzzy Deduplication)
# ============================================================
@app.route("/find_food")
def find_food():
    location = "沙鹿"
    encoded_location = urllib.parse.quote(location)
    url = f"https://www.ptt.cc/bbs/Food/search?q={encoded_location}"
    cookies = {'over18': '1'}
    headers = {"User-Agent": "Mozilla/5.0"}
    
    total_inserted = 0
    try:
        safe_init_firebase()
        db = firestore.client()

        # 💡 核心技術：先撈出資料庫現有的所有店名，用來做「包含比對」
        existing_docs = db.collection("restaurants").get()
        existing_names = [doc.id for doc in existing_docs] # 抓取所有的 Document ID

        while url:
            response = requests.get(url, headers=headers, cookies=cookies)
            if response.status_code != 200: break
            soup = BeautifulSoup(response.text, 'html.parser')
            articles = soup.find_all('div', class_='r-ent')
            if not articles: break

            for art in articles:
                title_tag = art.find('div', class_='title')
                if title_tag and title_tag.a:
                    title_text = title_tag.a.text.strip()
                    if "[食記]" not in title_text: continue
                    
                    new_name = super_clean_title(title_text)
                    if not new_name or len(new_name) <= 1: continue

                    # 💡 智慧去重：檢查新店名是否與現有店名「互相包含」
                    # 比如「炳修豆漿」與「炳修永和豆漿」會被視為同一家
                    matched_id = new_name
                    for ex_name in existing_names:
                        if ex_name in new_name or new_name in ex_name:
                            # 如果有包含關係，統一使用較短的那個（通常是品牌主名）
                            matched_id = ex_name if len(ex_name) < len(new_name) else new_name
                            break
                    
                    simulated_rating = round(random.uniform(4.0, 4.9), 1)
                    doc = {
                        "name": matched_id,
                        "ptt_title": title_text,
                        "area": location,
                        "rating": simulated_rating,
                        "type": "美食",
                        "sync_time": firestore.SERVER_TIMESTAMP
                    }

                    # 使用 matched_id 進行寫入，若重複則會自動覆蓋，不會產生新資料
                    db.collection("restaurants").document(matched_id).set(doc)
                    
                    # 如果是新名字，加入快取清單避免同一輪爬蟲內重複
                    if matched_id not in existing_names:
                        existing_names.append(matched_id)
                        total_inserted += 1
            
            # 翻頁邏輯
            btn_tags = soup.find_all('a', class_='btn wide')
            url = None
            for btn in btn_tags:
                if "上頁" in btn.text and 'href' in btn.attrs:
                    url = "https://www.ptt.cc" + btn['href']
                    break
            time.sleep(0.3)
            # 限制最多翻 5 頁避免 Vercel 超時
            if total_inserted > 100: break

        return render_template("result.html", total_inserted=total_inserted, total_in_db=len(existing_names))
    except Exception as e:
        return f"❌ 異常：{e}"

# ============================================================
# 🗑️ 4. 清空資料庫
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
        return f"<h3>🧹 重置成功</h3><p>已清空 {count} 筆資料。</p><a href='/'>返回</a>"
    except Exception as e: return f"失敗: {e}"

# ============================================================
# 🤖 5. Webhook (地理物件解析 + 智慧分類)
# ============================================================
@app.route("/webhook", methods=["POST"])
def webhook():
    req = request.get_json(force=True)
    params = req.get("queryResult", {}).get("parameters", {})
    action = req.get("queryResult", {}).get("action", "")
    
    # 地理物件解析
    raw_loc = params.get("location", "沙鹿")
    if isinstance(raw_loc, dict):
        user_loc = (raw_loc.get("subadmin-area") or raw_loc.get("city") or "沙鹿").replace("區","").replace("市","")
    else:
        user_loc = str(raw_loc).replace("區","").replace("市","")

    user_food_type = params.get("food_type", "")
    info = "抱歉，目前無法處理。"

    if action == "recommend_restaurant":
        try:
            safe_init_firebase()
            db = firestore.client()
            docs = db.collection("restaurants").get()
            all_r = [d.to_dict() for d in docs]
            
            # 過濾地區
            filtered = [r for r in all_r if r.get("area") == user_loc]
            
            # 關鍵字智慧聯想
            type_map = {
                "宵夜": ["宵夜", "深夜", "燒烤", "豆漿"],
                "下午茶": ["甜點", "咖啡", "蛋糕", "冰"],
                "早午餐": ["早餐", "蛋餅", "漢堡", "飯糰"]
            }
            
            if user_food_type in type_map:
                keywords = type_map[user_food_type]
                filtered = [r for r in filtered if any(kw in r.get("ptt_title", "") for kw in keywords)]

            if filtered:
                res = random.sample(filtered, min(3, len(filtered)))
                info = f"🤖 為您精選【{user_loc} {user_food_type}】：\n\n"
                for i, r in enumerate(res, 1):
                    info += f"🍱 {i}. {r['name']}\n⭐ 評分：{r['rating']}\n\n"
            else:
                info = f"📋 找不到【{user_loc} {user_food_type}】的資料。"
        except Exception as e: info = f"出錯: {e}"
            
    return make_response(jsonify({"fulfillmentText": info}))

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=5000)