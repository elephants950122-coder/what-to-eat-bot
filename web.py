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
# 🧼 終極資料清洗器：精準斬斷所有形容詞與雜質，只留純店名
# ============================================================
def clean_restaurant_name(raw_title):
    # 1. 移除 PTT 基本標籤與地區
    name = raw_title.replace("[食記]", "").replace("台中", "").replace("沙鹿", "")
    
    # 2. 徹底拔除所有可能出現的特殊干擾符號與空格
    garbage_symbols = ["-", "—", "[", "]", "【", "】", "(", ")", "（", "）", "～", "~", "：", ":", "/", "\\", "、", ".", " ", "  "]
    for symbol in garbage_symbols:
        name = name.replace(symbol, "")
        
    # 3. 💡 關鍵切斷點：遇到以下食記常見的心得或贅詞，直接「只取左邊」，把右邊雜質一刀切斷！
    cut_off_words = [
        "吃到飽", "平價", "便當", "餐車", "火鍋", "創始店", "老店", "美食", "下午茶", 
        "夜景", "景觀", "推薦", "早午餐", "宵夜", "小吃", "新開幕", "風味餐", "初訪"
    ]
    for word in cut_off_words:
        if word in name:
            name = name.split(word)[0] # 只留下贅詞前面的純店名
            
    name = name.strip()
    
    # 如果清洗完太短或太長，給予安全限制
    if not name or len(name) <= 1:
        return ""
    return name[:10] # 正常店名通常在 10 個字以內

# ============================================================
# 🏠 1. 首頁路由
# ============================================================
@app.route("/")
def home():
    return render_template("index.html")

# ============================================================
# 📡 2. 爬蟲路由 (超強模糊比對去重版)
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
        
        # 先撈出目前資料庫裡「已經存在的所有餐廳」名單，用來做現場模糊比對
        existing_docs = db.collection("restaurants").get()
        # 建立一個現存店名的字典 { "店名": "文件ID" }
        existing_refs = {doc.to_dict().get("name"): doc.id for doc in existing_docs if doc.to_dict().get("name")}
        
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
                    
                    # 💡 調用終極清洗器，產出極度純淨的店名 (例如：大喜鍋)
                    clean_name = clean_restaurant_name(title_text)
                    
                    if not clean_name: 
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
                    
                    # 💡 終極去重邏輯：檢查「新店名」是否與「現存店名」互相包含
                    duplicate_id = None
                    for ex_name, ex_id in existing_refs.items():
                        # 如果新清洗出的名字包含在舊名字裡 (大喜鍋 inside 大喜鍋吃到飽)
                        # 或者舊名字包含在新名字裡
                        if clean_name in ex_name or ex_name in clean_name:
                            duplicate_id = ex_id
                            break
                    
                    if duplicate_id:
                        # 判定為重複店家！直接覆蓋更新該筆文件，絕不疊加
                        db.collection("restaurants").document(duplicate_id).update(doc)
                    else:
                        # 確定是全新未見過的店家，直接 add，並把新名字加進比對字典中
                        new_doc_ref = db.collection("restaurants").add(doc)
                        existing_refs[clean_name] = new_doc_ref[1].id
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