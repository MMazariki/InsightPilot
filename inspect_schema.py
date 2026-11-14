import json

with open(
    "fabric_iq_schema.json",
    "r",
    encoding="utf-8"
) as f:
    data = json.load(f)

print("TOP-LEVEL KEYS:")
print(list(data.keys()))

print("\nSTRUCTURED CONTENT:")
print(
    json.dumps(
        data.get("structuredContent", {}),
        indent=2,
        ensure_ascii=False
    )
)

print("\nTEXT CONTENT:")
for item in data.get("content", []):
    if item.get("type") == "text":
        print(item.get("text", ""))