from sqlalchemy import select

from app.discord_bridge import AdminBridge, InboundAttachment, InboundMessage
from app.models.canon_import import CanonImport
from app.models.location import Location


def _message(content, *, author="owner", attachment=None):
    return InboundMessage(discord_message_id=content + author, guild_id="g", channel_id="c",
        author_discord_id=author, author_display_name=author, content=content,
        attachments=(attachment,) if attachment else ())


async def _campaign(admin):
    return await admin.handle(_message("!rv campaign new Imported World"))


async def test_owner_stages_reviews_and_approves_json_world(db, provider):
    admin = AdminBridge(db, provider)
    await _campaign(admin)
    data = b'''{"version":1,"locations":[
      {"key":"tavern","name":"Grey Wolf Tavern","obvious":"A warm common room.","hidden":"A smugglers hatch.","connections":["square"]},
      {"key":"square","name":"Village Square","obvious":"A stone well stands outside.","connections":["tavern"]}
    ]}'''
    result = await admin.handle(_message("!rv campaign import", attachment=InboundAttachment("world.json", "application/json", data)))
    assert "Nothing is canon yet" in result.responses[0].content
    async with db.session() as s:
        draft = (await s.execute(select(CanonImport))).scalar_one()
        assert draft.status == "PENDING_REVIEW"
        assert not list((await s.execute(select(Location))).scalars())
    result = await admin.handle(_message(f"!rv campaign import approve {draft.id}"))
    assert "Grey Wolf Tavern" in result.responses[0].content
    async with db.session() as s:
        locations = list((await s.execute(select(Location).order_by(Location.name))).scalars())
        assert len(locations) == 2
        tavern = next(x for x in locations if x.name == "Grey Wolf Tavern")
        square = next(x for x in locations if x.name == "Village Square")
        assert tavern.connections == [f"location:{square.id}"]
        assert tavern.description_hidden == "A smugglers hatch."


async def test_non_owner_cannot_import(db, provider):
    admin = AdminBridge(db, provider); await _campaign(admin)
    await admin.handle(_message("!rv join", author="player"))
    attachment = InboundAttachment("world.json", "application/json", b'{"locations":[]}')
    result = await admin.handle(_message("!rv campaign import", author="player", attachment=attachment))
    assert "Only the campaign owner" in result.responses[0].content


async def test_markdown_requires_explicit_sections_and_review(db, provider):
    admin = AdminBridge(db, provider); await _campaign(admin)
    text = """# My World
## Location: Forest Gate
### Key
forest-gate
### Obvious
An old gate opens toward the forest.
### Connections
village
## Location: Village
### Key
village
### Obvious
Low roofs cluster around a well.
### Connections
forest-gate
""".encode()
    result = await admin.handle(_message("!rv campaign import", attachment=InboundAttachment("world.md", "text/markdown", text)))
    assert "Forest Gate" in result.responses[0].content
