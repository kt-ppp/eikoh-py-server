from itertools import count

from fastapi import HTTPException

posts: list[dict] = []
next_post_id = count(1)


def validated_text(text: str) -> str:
    if not isinstance(text, str) or not 1 <= len(text.strip()) <= 500:
        raise HTTPException(400, "投稿は1〜500文字で入力してください。")
    return text.strip()


def add_post(user: dict, text: str) -> dict:
    text = validated_text(text)
    post = {"id": next(next_post_id), "user_id": user["user_id"], "name": user["name"], "text": text}
    posts.append(post)
    del posts[:-100]
    return post


def owned_post(user: dict, post_id: int) -> dict:
    if type(post_id) is not int or post_id <= 0:
        raise HTTPException(400, "投稿の番号が不正です。")
    post = next((post for post in posts if post["id"] == post_id), None)
    if post is None:
        raise HTTPException(404, "投稿が見つかりません。")
    if post["user_id"] != user["user_id"]:
        raise HTTPException(403, "自分の投稿だけ編集・削除できます。")
    return post


def edit_post(user: dict, post_id: int, text: str) -> dict:
    post = owned_post(user, post_id)
    post["text"] = validated_text(text)
    return post


def delete_post(user: dict, post_id: int) -> dict:
    post = owned_post(user, post_id)
    posts.remove(post)
    return post
