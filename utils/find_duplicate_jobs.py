from pymongo import MongoClient

# --- Configuration ---
MONGO_URI = "mongodb://127.0.0.1:27017/"  # Your MongoDB connection string
DATABASE_NAME = "willow_database"
COLLECTION_NAME = "jobs"
KEY_TO_CHECK = "job_id"

# --- Script ---
def find_duplicate_keys(mongo_uri, db_name, collection_name, key_to_check):
    """
    Connects to MongoDB and finds duplicate entries based on a specified key.

    Args:
        mongo_uri (str): The MongoDB connection string.
        db_name (str): The name of the database.
        collection_name (str): The name of the collection.
        key_to_check (str): The key to check for duplicate values.

    Returns:
        list: A list of dictionaries, where each dictionary contains the
              duplicate key value and the count of occurrences.
    """
    client = None
    try:
        client = MongoClient(mongo_uri)
        db = client[db_name]
        collection = db[collection_name]

        # Aggregation pipeline to find duplicates
        pipeline = [
            {
                "$group": {
                    "_id": f"${key_to_check}",  # Group by the value of the specified key
                    "count": { "$sum": 1 }     # Count the occurrences of each key value
                }
            },
            {
                "$match": {
                    "count": { "$gt": 1 }      # Filter for key values that appear more than once
                }
            }
            # You can add a $project stage here if you only want specific fields
            # for the duplicate entries, e.g., {"_id": 0, "duplicate_value": "$_id", "count": 1}
        ]

        duplicate_entries = list(collection.aggregate(pipeline))

        return duplicate_entries

    except Exception as e:
        print(f"An error occurred: {e}")
        return []
    finally:
        if client:
            client.close()

if __name__ == "__main__":
    duplicates = find_duplicate_keys(MONGO_URI, DATABASE_NAME, COLLECTION_NAME, KEY_TO_CHECK)

    if duplicates:
        print(f"Found duplicate entries in '{COLLECTION_NAME}' based on the key '{KEY_TO_CHECK}':")
        for entry in duplicates:
            print(f"  Value: {entry['_id']}, Count: {entry['count']}")
    else:
        print(f"No duplicate entries found in '{COLLECTION_NAME}' based on the key '{KEY_TO_CHECK}'.")
