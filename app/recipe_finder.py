import os
import aiohttp
import asyncio
from typing import List, Dict

from dotenv import load_dotenv

load_dotenv()

class RecipeFinder:
    # Bing Search API endpoint and subscription key.
    # Replace 'YOUR_BING_API_KEY' with your actual key.
    BING_SEARCH_URL = "https://api.bing.microsoft.com/v7.0/search"
    BING_API_KEY = os.getenv("BING_SEARCH_V7_API_KEY")

    @staticmethod
    async def find_recipe(cuisine: str, ingredients: List[str]) -> List[Dict]:
        # Check if Bing API key is available
        if not RecipeFinder.BING_API_KEY:
            print("⚠️  Bing API key not found. Returning dummy recipe data for demo purposes.")
            return RecipeFinder._get_dummy_recipes(cuisine, ingredients)
        
        # Construct the query to search within seriouseats.com for recipes.
        # The query includes the cuisine, the word 'recipe', and the ingredients.
        ingredients_query = " ".join(ingredients)
        query = f"site:seriouseats.com {cuisine} recipe {ingredients_query}"

        headers = {"Ocp-Apim-Subscription-Key": RecipeFinder.BING_API_KEY}
        params = {
            "q": query,
            "count": 5,               # limit results to 5 (adjust as needed)
            "textDecorations": "True",
            "textFormat": "HTML"
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(RecipeFinder.BING_SEARCH_URL, headers=headers, params=params) as response:
                    if response.status != 200:
                        print(f"⚠️  Bing search API error: {response.status}. Falling back to dummy data.")
                        return RecipeFinder._get_dummy_recipes(cuisine, ingredients)
                    data = await response.json()

                    # Check if the response contains web page results.
                    if "webPages" not in data or "value" not in data["webPages"]:
                        return RecipeFinder._get_dummy_recipes(cuisine, ingredients)

                    # Extract recipe information from the results.
                    recipes = []
                    for item in data["webPages"]["value"]:
                        recipes.append({
                            "name": item.get("name"),
                            "url": item.get("url"),
                            "snippet": item.get("snippet")
                        })
                    return recipes
        except Exception as e:
            print(f"⚠️  Error calling Bing API: {e}. Falling back to dummy data.")
            return RecipeFinder._get_dummy_recipes(cuisine, ingredients)

    @staticmethod
    def _get_dummy_recipes(cuisine: str, ingredients: List[str]) -> List[Dict]:
        """Return dummy recipe data for demo purposes when Bing API is not available."""
        ingredients_str = ", ".join(ingredients[:3])  # Use first 3 ingredients
        
        dummy_recipes = [
            {
                "name": f"Classic {cuisine} {ingredients[0].capitalize()} Recipe",
                "url": "https://www.seriouseats.com/demo-recipe-1",
                "snippet": f"A delicious {cuisine.lower()} recipe featuring {ingredients_str}. This traditional dish combines fresh ingredients with authentic cooking techniques for an amazing flavor profile."
            },
            {
                "name": f"Quick {cuisine} Stir-fry with {ingredients[0].capitalize()}",
                "url": "https://www.seriouseats.com/demo-recipe-2", 
                "snippet": f"An easy 30-minute {cuisine.lower()} stir-fry recipe using {ingredients_str}. Perfect for weeknight dinners with bold flavors and simple preparation."
            },
            {
                "name": f"Traditional {cuisine} {ingredients[0].capitalize()} Soup",
                "url": "https://www.seriouseats.com/demo-recipe-3",
                "snippet": f"A hearty {cuisine.lower()} soup recipe with {ingredients_str}. Comfort food at its finest, this recipe has been passed down through generations."
            }
        ]
        
        # Add more variety based on cuisine type
        if cuisine.lower() in ["italian", "pasta"]:
            dummy_recipes.append({
                "name": f"Creamy {ingredients[0].capitalize()} Pasta Primavera",
                "url": "https://www.seriouseats.com/demo-pasta-recipe",
                "snippet": f"Fresh pasta tossed with {ingredients_str} in a light cream sauce. Restaurant-quality Italian cooking made simple at home."
            })
        elif cuisine.lower() in ["mexican", "spanish"]:
            dummy_recipes.append({
                "name": f"{ingredients[0].capitalize()} Tacos with Fresh Salsa",
                "url": "https://www.seriouseats.com/demo-taco-recipe", 
                "snippet": f"Authentic street-style tacos featuring {ingredients_str}. Served with homemade salsa and traditional accompaniments."
            })
        elif cuisine.lower() in ["asian", "chinese", "thai"]:
            dummy_recipes.append({
                "name": f"Spicy {ingredients[0].capitalize()} Curry",
                "url": "https://www.seriouseats.com/demo-curry-recipe",
                "snippet": f"Aromatic {cuisine.lower()} curry with {ingredients_str}. Rich coconut milk base with traditional spices and herbs."
            })
        
        return dummy_recipes[:3]  # Return up to 3 recipes

# ## Example usage:
async def main():
    # Example query: Italian recipes with tomato, basil, and mozzarella
    cuisine = "Italian"
    ingredients = ["tomato", "basil", "mozzarella", "pasta"]

    try:
        recipes = await RecipeFinder.find_recipe(cuisine, ingredients)
        if recipes:
            print("🍳 Found recipes:")
            for recipe in recipes:
                print(f"📖 Title: {recipe['name']}")
                print(f"🔗 URL: {recipe['url']}")
                print(f"📝 Description: {recipe['snippet']}\n")
        else:
            print("No recipes found.")
    except Exception as e:
        print(f"An error occurred: {e}")

if __name__ == "__main__":
    asyncio.run(main())