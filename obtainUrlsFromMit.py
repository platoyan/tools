import requests
import json
import time

BASE = "https://api.learn.mit.edu/api/v1/learning_resources_search/"

def fetch_all_ocw():
    params = {
        "aggregations": [
            "resource_type",
            "certification_type",
            "delivery",
            "department",
            "topic",
            "offered_by",
            "free",
            "professional",
            "resource_category",
            "resource_type_group",
        ],
        "department": "18",          # Mathematics
        "free": "true",
        "limit": 100,
        "offered_by": "ocw",
        "offset": 0,
        "q": "",
        "resource_type_group": "course",
        "show_ocw_files": "false"
    }
    
    out = []
    while True:
        r = requests.get(BASE, params=params, timeout=30)
        r.raise_for_status()
        data = r.json()
        
        for item in data["results"]:
            # runs 是排课实例(各学期),取第一个或最新的
            runs = item.get("runs") or []
            run = runs[0] if runs else {}
            out.append({
                "id": item.get("id"),
                "readable_id": item.get("readable_id"),
                "title": item.get("title"),
                "url": item.get("url"),
                "description": item.get("description"),
                "topics": [t["name"] for t in (item.get("topics") or [])],
                "level": run.get("level"),
                "year": run.get("year"),
                "semester": run.get("semester"),
                "instructors": [
                    i.get("full_name") for i in (run.get("instructors") or [])
                ],
            })
        
        if not data.get("next"):
            break
        params["offset"] += params["limit"]
        time.sleep(0.3)
    
    return out


if __name__ == "__main__":
    courses = fetch_all_ocw_math()
    print(f"共 {len(courses)} 门")
    with open("ocw_math.json", "w", encoding="utf-8") as f:
        json.dump(courses, f, ensure_ascii=False, indent=2)