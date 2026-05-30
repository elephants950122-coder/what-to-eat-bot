import os
import json
import random
from flask import Flask, request, jsonify, make_response
import firebase_admin
from firebase_admin import credentials, firestore

app = Flask(__name__)

# 💡 終極修正：在最外層先宣告全域變數 db，確保整個程式（包括 Webhook）都絕對認得它！
db = None

# ============================================================
# 🔑 初始化 Firebase (環境變數安全版)
# ============================================================
try:
    if not firebase_admin._apps:
        if "FIREBASE_KEY" in os.environ:
            print("📡 偵測到雲端環境變數，正在解析金鑰...")
            key_dict = json.loads(os.environ["FIREBASE_KEY"])
            cred = credentials.Certificate(key_dict)
        else:
            print("🏠 偵測為本地環境，正在讀取 serviceAccountKey.json...")
            cred = credentials.Certificate("serviceAccountKey.json")
            
        firebase_admin.initialize_app(cred)
    
    # 💡 確保 db 在這裡被正確指派給全域
    db = firestore.client()
    print("✅ [成功] Firebase 已經連線成功！")
except Exception as e:
    print(f"❌ [失敗] Firebase 初始化失敗: {e}")

@app.route("/")
def home():
    return "<h1>🍱 沙鹿美食大數據伺服器</h1><p>狀態：雲端運行中</p>"

# ============================================================
# 🤖 Webhook 核心修正版 (強固型態防錯)
# ============================================================
@app.route("/webhook", methods=["POST"])
def webhook():
    global db # 💡 再次明確告訴 Flask，我們要用最外面的那個 db 連線
    
    req = request.get_json(force=True)
    action = req.get("queryResult", {}).get("action", "")
    
    # 預設回覆
    info = "抱歉，目前無法從資料庫獲取美食清單。"

    if action == "recommend_restaurant":
        try:
            # 檢查 db 是否有成功連線
            if db is None:
                return make_response(jsonify({"fulfillmentText": "❌ 後端錯誤：資料庫連線未建立成功。"}))

            # 從 Firebase 抓取所有資料
            docs = db.collection("restaurants").get()
            all_restaurants = [doc.to_dict() for doc in docs]

            if all_restaurants:
                # 隨機抽出 5 家店
                sample_size = min(5, len(all_restaurants))
                random_list = random.sample(all_restaurants, sample_size)
                
                result_text = "🔎 根據資料庫大數據，為您精選沙鹿美食：\n\n"
                for index, item in enumerate(random_list, 1):
                    name = str(item.get("name", "未知店家"))
                    rating = str(item.get("rating", "4.0"))
                    title = str(item.get("ptt_title", "無標題"))
                    
                    result_text += f"🍱 推薦 {index}：{name}\n"
                    result_text += f"⭐ 鄉民評分：{rating}\n"
                    result_text += f"💬 來源：{title}\n\n"
                
                info = result_text + "祝您用餐愉快！😋"
            else:
                info = "📋 目前資料庫內沒資料，請先連線到 /find_food 進行爬取。"
                
        except Exception as e:
            info = f"❌ 後端執行錯誤，原因: {str(e)}"

    elif action == "GetFoodList":
        try:
            if db is None:
                return make_response(jsonify({"fulfillmentText": "❌ 後端錯誤：資料庫連線未建立成功。"}))
                
            docs = db.collection("restaurants").get()
            titles = []
            for doc in docs:
                data = doc.to_dict()
                if data and data.get("name"):
                    titles.append(str(data.get("name")))
                    
            if titles:
                unique_titles = list(set(titles))
                info = "📋 目前資料庫收錄的沙鹿美食：\n\n- " + "\n- ".join(unique_titles[:30])
            else:
                info = "資料庫目前沒有資料。"
        except Exception as e:
            info = f"❌ 讀取清單失敗，原因: {str(e)}"

    # 包裝回傳
    response_data = {"fulfillmentText": info}
    res = make_response(jsonify(response_data))
    res.headers['Content-Type'] = 'application/json'
    return res

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=5000, debug=True)