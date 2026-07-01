import discord
from discord.ext import commands
import os
import re
from datetime import datetime
import asyncio
import psycopg
from psycopg_pool import ConnectionPool
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Bot Setup
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='!', intents=intents, help_command=None)

# Database Setup
DATABASE_URL = os.getenv('DATABASE_URL')
DISCORD_TOKEN = os.getenv('DISCORD_TOKEN')

# Connection Pool
db_pool = None

def init_db_pool():
    """Initialize database connection pool"""
    global db_pool
    try:
        db_pool = ConnectionPool(conninfo=DATABASE_URL, min_size=1, max_size=20)
        print("✅ Database connection pool initialized")
    except Exception as e:
        print(f"❌ Database connection error: {e}")
        return False
    return True

def get_db_connection():
    """Get a connection from the pool"""
    try:
        return db_pool.getconn()
    except Exception as e:
        print(f"❌ Error getting connection: {e}")
        return None

def return_db_connection(conn):
    """Return connection to the pool"""
    if conn:
        db_pool.putconn(conn)

def create_tables():
    """Create database tables if they don't exist"""
    conn = get_db_connection()
    if not conn:
        return False

    try:
        cur = conn.cursor()

        # Staff table
        cur.execute("""
            CREATE TABLE IF NOT EXISTS staff (
                staff_id TEXT PRIMARY KEY,
                name VARCHAR(100) NOT NULL,
                weekly_points INTEGER DEFAULT 0,
                total_points INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Weekly reset log
        cur.execute("""
            CREATE TABLE IF NOT EXISTS weekly_resets (
                id SERIAL PRIMARY KEY,
                reset_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        conn.commit()
        print("✅ Database tables created/verified")
        return True
    except Exception as e:
        print(f"❌ Error creating tables: {e}")
        conn.rollback()
        return False
    finally:
        return_db_connection(conn)

def get_rating(points):
    """Get rating based on points"""
    if points < 1000:
        return "❌ REMOVE"
    elif 1000 <= points < 1700:
        return "⚠️ BAD"
    elif 1700 <= points < 2000:
        return "📊 OKAY"
    else:
        return "✅ GOOD"

def create_activity_embed():
    """Create embed showing current week activity"""
    embed = discord.Embed(
        title="📊 Staff Activity Check List - Weekly Report",
        color=discord.Color.blue(),
        timestamp=datetime.now()
    )

    conn = get_db_connection()
    if not conn:
        embed.description = "❌ Database connection error"
        return embed

    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT name, weekly_points
            FROM staff
            ORDER BY weekly_points DESC
        """)

        staff_list = cur.fetchall()

        if not staff_list:
            embed.description = "No staff members tracked yet."
            return embed

        description = ""
        for i, (name, points) in enumerate(staff_list, 1):
            rating = get_rating(points)
            description += f"{i}. **{name}** - {points} pts {rating}\n"

        embed.description = description
        embed.add_field(
            name="📋 Rating System",
            value="❌ REMOVE: < 1000 pts\n⚠️ BAD: 1000-1699 pts\n📊 OKAY: 1700-1999 pts\n✅ GOOD: 2000+ pts",
            inline=False
        )

        return embed
    except Exception as e:
        embed.description = f"❌ Error: {e}"
        return embed
    finally:
        return_db_connection(conn)

def parse_bulk_weekly_block(raw_text):
    """
    Parses blocks like:
        2. @perm support only
        Points: 147715 | Available: 45715
    into a list of (name, points) tuples. Ignores 'Available'.
    Handles optional leading numbering ('2.', '10.') and optional '@'.
    """
    lines = [l.strip() for l in raw_text.splitlines() if l.strip()]
    results = []
    i = 0
    name_line_re = re.compile(r"^(?:\d+\.\s*)?@?(.+)$")
    points_line_re = re.compile(r"Points:\s*(-?\d+)", re.IGNORECASE)

    while i < len(lines):
        line = lines[i]
        points_match = points_line_re.search(line)
        if points_match:
            # A "Points:" line without a preceding name line - skip it
            i += 1
            continue

        name_match = name_line_re.match(line)
        if name_match and i + 1 < len(lines):
            next_line = lines[i + 1]
            pts_match = points_line_re.search(next_line)
            if pts_match:
                name = name_match.group(1).strip()
                points = int(pts_match.group(1))
                results.append((name, points))
                i += 2
                continue
        i += 1

    return results

@bot.event
async def on_ready():
    print(f'✅ Bot logged in as {bot.user}')
    activity = discord.Activity(type=discord.ActivityType.watching, name="!help for commands")
    await bot.change_presence(activity=activity)

@bot.command(name='add_staff')
@commands.has_permissions(administrator=True)
async def add_staff(ctx, member: discord.Member, name: str = None):
    """Add a staff member to track"""
    staff_name = name if name else member.display_name
    staff_id = str(member.id)

    conn = get_db_connection()
    if not conn:
        embed = discord.Embed(
            title="❌ Database Error",
            description="Cannot connect to database",
            color=discord.Color.red()
        )
        await ctx.send(embed=embed)
        return

    try:
        cur = conn.cursor()

        # Check if already exists
        cur.execute("SELECT name FROM staff WHERE staff_id = %s", (staff_id,))
        if cur.fetchone():
            embed = discord.Embed(
                title="❌ Error",
                description=f"{staff_name} is already being tracked!",
                color=discord.Color.red()
            )
            await ctx.send(embed=embed)
            return

        # Add staff
        cur.execute(
            """INSERT INTO staff (staff_id, name, weekly_points, total_points)
               VALUES (%s, %s, 0, 0)""",
            (staff_id, staff_name)
        )
        conn.commit()

        embed = discord.Embed(
            title="✅ Staff Added",
            description=f"Added **{staff_name}** to activity tracking!",
            color=discord.Color.green()
        )
        await ctx.send(embed=embed)
    except Exception as e:
        embed = discord.Embed(
            title="❌ Error",
            description=f"Error: {str(e)}",
            color=discord.Color.red()
        )
        await ctx.send(embed=embed)
        conn.rollback()
    finally:
        return_db_connection(conn)

@bot.command(name='add_staff_manual')
@commands.has_permissions(administrator=True)
async def add_staff_manual(ctx):
    """Add a staff member by ID (interactive - for members from other servers)"""

    # Ask for name
    embed = discord.Embed(
        title="➕ Add Staff Member",
        description="What is the staff member's **name**?\n(Type the name)",
        color=discord.Color.blue()
    )
    await ctx.send(embed=embed)

    try:
        name_msg = await bot.wait_for(
            'message',
            timeout=30,
            check=lambda m: m.author == ctx.author and m.channel == ctx.channel
        )
        name = name_msg.content.strip()
    except asyncio.TimeoutError:
        embed = discord.Embed(
            title="⏰ Timeout",
            description="You took too long!",
            color=discord.Color.red()
        )
        await ctx.send(embed=embed)
        return

    # Ask for ID
    embed = discord.Embed(
        title="➕ Add Staff Member",
        description=f"**Name:** {name}\n\nNow, what is their **Discord User ID**?\n(Copy from right-click → Copy User ID)",
        color=discord.Color.blue()
    )
    await ctx.send(embed=embed)

    try:
        id_msg = await bot.wait_for(
            'message',
            timeout=30,
            check=lambda m: m.author == ctx.author and m.channel == ctx.channel
        )
        staff_id = id_msg.content.strip()
        int(staff_id)
    except asyncio.TimeoutError:
        embed = discord.Embed(
            title="⏰ Timeout",
            description="You took too long!",
            color=discord.Color.red()
        )
        await ctx.send(embed=embed)
        return
    except ValueError:
        embed = discord.Embed(
            title="❌ Invalid ID",
            description="That doesn't look like a valid Discord ID!",
            color=discord.Color.red()
        )
        await ctx.send(embed=embed)
        return

    conn = get_db_connection()
    if not conn:
        embed = discord.Embed(
            title="❌ Database Error",
            description="Cannot connect to database",
            color=discord.Color.red()
        )
        await ctx.send(embed=embed)
        return

    try:
        cur = conn.cursor()

        # Check if already exists
        cur.execute("SELECT name FROM staff WHERE staff_id = %s", (staff_id,))
        if cur.fetchone():
            embed = discord.Embed(
                title="❌ Already Exists",
                description=f"**{name}** (ID: {staff_id}) is already being tracked!",
                color=discord.Color.red()
            )
            await ctx.send(embed=embed)
            return

        # Add staff
        cur.execute(
            """INSERT INTO staff (staff_id, name, weekly_points, total_points)
               VALUES (%s, %s, 0, 0)""",
            (staff_id, name)
        )
        conn.commit()

        embed = discord.Embed(
            title="✅ Staff Added!",
            description=f"Successfully added **{name}** to the system!",
            color=discord.Color.green()
        )
        await ctx.send(embed=embed)
    except Exception as e:
        embed = discord.Embed(
            title="❌ Error",
            description=f"Error: {str(e)}",
            color=discord.Color.red()
        )
        await ctx.send(embed=embed)
        conn.rollback()
    finally:
        return_db_connection(conn)

@bot.command(name='add_points')
@commands.has_permissions(administrator=True)
async def add_points(ctx, member: discord.Member, points: int):
    """Add points to a staff member"""
    staff_id = str(member.id)

    if points < 0:
        embed = discord.Embed(
            title="❌ Error",
            description="Points must be positive!",
            color=discord.Color.red()
        )
        await ctx.send(embed=embed)
        return

    conn = get_db_connection()
    if not conn:
        embed = discord.Embed(
            title="❌ Database Error",
            description="Cannot connect to database",
            color=discord.Color.red()
        )
        await ctx.send(embed=embed)
        return

    try:
        cur = conn.cursor()

        # Check if exists
        cur.execute("SELECT name, weekly_points FROM staff WHERE staff_id = %s", (staff_id,))
        result = cur.fetchone()

        if not result:
            embed = discord.Embed(
                title="❌ Error",
                description=f"{member.display_name} is not being tracked! Use !add_staff first.",
                color=discord.Color.red()
            )
            await ctx.send(embed=embed)
            return

        name, current_points = result
        new_points = current_points + points

        # Update points
        cur.execute(
            "UPDATE staff SET weekly_points = %s, updated_at = CURRENT_TIMESTAMP WHERE staff_id = %s",
            (new_points, staff_id)
        )
        conn.commit()

        rating = get_rating(new_points)

        embed = discord.Embed(
            title="✅ Points Added",
            description=f"Added **{points}** points to {name}\n\n**Current Points:** {new_points}\n**Rating:** {rating}",
            color=discord.Color.green()
        )
        await ctx.send(embed=embed)
    except Exception as e:
        embed = discord.Embed(
            title="❌ Error",
            description=f"Error: {str(e)}",
            color=discord.Color.red()
        )
        await ctx.send(embed=embed)
        conn.rollback()
    finally:
        return_db_connection(conn)

@bot.command(name='set_points')
@commands.has_permissions(administrator=True)
async def set_points(ctx, member: discord.Member, points: int):
    """Set exact points for a staff member"""
    staff_id = str(member.id)

    if points < 0:
        embed = discord.Embed(
            title="❌ Error",
            description="Points must be positive!",
            color=discord.Color.red()
        )
        await ctx.send(embed=embed)
        return

    conn = get_db_connection()
    if not conn:
        embed = discord.Embed(
            title="❌ Database Error",
            description="Cannot connect to database",
            color=discord.Color.red()
        )
        await ctx.send(embed=embed)
        return

    try:
        cur = conn.cursor()

        # Check if exists
        cur.execute("SELECT name, weekly_points FROM staff WHERE staff_id = %s", (staff_id,))
        result = cur.fetchone()

        if not result:
            embed = discord.Embed(
                title="❌ Error",
                description=f"{member.display_name} is not being tracked! Use !add_staff first.",
                color=discord.Color.red()
            )
            await ctx.send(embed=embed)
            return

        name, old_points = result

        # Update points
        cur.execute(
            "UPDATE staff SET weekly_points = %s, updated_at = CURRENT_TIMESTAMP WHERE staff_id = %s",
            (points, staff_id)
        )
        conn.commit()

        rating = get_rating(points)

        embed = discord.Embed(
            title="✅ Points Updated",
            description=f"Updated {name}'s points\n\n**Old Points:** {old_points}\n**New Points:** {points}\n**Rating:** {rating}",
            color=discord.Color.green()
        )
        await ctx.send(embed=embed)
    except Exception as e:
        embed = discord.Embed(
            title="❌ Error",
            description=f"Error: {str(e)}",
            color=discord.Color.red()
        )
        await ctx.send(embed=embed)
        conn.rollback()
    finally:
        return_db_connection(conn)

@bot.command(name='bulk_set_weekly')
@commands.has_permissions(administrator=True)
async def bulk_set_weekly(ctx, *, raw_list: str = None):
    """
    Paste a whole weekly list in one message and set weekly_points for everyone at once.

    Usage:
        !bulk_set_weekly
        @Luki
        Points: 258335 | Available: -9841666
        2. @perm support only
        Points: 147715 | Available: 45715
        ...

    Only the "Points:" value is used. "Available:" is ignored.
    Matches staff by their stored `name` (case-insensitive).
    """
    if not raw_list or not raw_list.strip():
        embed = discord.Embed(
            title="❌ Missing list",
            description="Paste the weekly list right after the command, e.g.\n"
                        "`!bulk_set_weekly`\n`@Luki`\n`Points: 258335 | Available: -9841666`\n`...`",
            color=discord.Color.red()
        )
        await ctx.send(embed=embed)
        return

    entries = parse_bulk_weekly_block(raw_list)
    if not entries:
        embed = discord.Embed(
            title="❌ Could not parse list",
            description="No `Name` + `Points: X` pairs were found. Check the format and try again.",
            color=discord.Color.red()
        )
        await ctx.send(embed=embed)
        return

    conn = get_db_connection()
    if not conn:
        embed = discord.Embed(
            title="❌ Database Error",
            description="Cannot connect to database",
            color=discord.Color.red()
        )
        await ctx.send(embed=embed)
        return

    updated = []
    not_found = []

    try:
        cur = conn.cursor()
        cur.execute("SELECT staff_id, name FROM staff")
        db_staff = cur.fetchall()
        # lowercase name -> staff_id
        name_lookup = {name.strip().lower(): staff_id for staff_id, name in db_staff}

        for name, points in entries:
            key = name.strip().lower()
            staff_id = name_lookup.get(key)
            if not staff_id:
                not_found.append((name, points))
                continue

            cur.execute(
                "UPDATE staff SET weekly_points = %s, updated_at = CURRENT_TIMESTAMP WHERE staff_id = %s",
                (points, staff_id)
            )
            updated.append((name, points))

        conn.commit()
    except Exception as e:
        conn.rollback()
        embed = discord.Embed(
            title="❌ Error",
            description=f"Error: {str(e)}",
            color=discord.Color.red()
        )
        await ctx.send(embed=embed)
        return
    finally:
        return_db_connection(conn)

    desc = f"**Updated {len(updated)} staff member(s):**\n"
    desc += "\n".join(f"• {n} → {p} pts" for n, p in updated) if updated else "_none_"

    embed = discord.Embed(
        title="✅ Bulk Weekly Set Complete",
        description=desc,
        color=discord.Color.green()
    )
    await ctx.send(embed=embed)

    if not_found:
        nf_desc = "\n".join(f"• {n} ({p} pts)" for n, p in not_found)
        nf_embed = discord.Embed(
            title="⚠️ Not Found (add them first)",
            description=f"These names don't match anyone in the database. Use `!add_staff_manual` to add them, then set their points manually:\n\n{nf_desc}",
            color=discord.Color.orange()
        )
        await ctx.send(embed=nf_embed)

@bot.command(name='batch_add')
@commands.has_permissions(administrator=True)
async def batch_add(ctx):
    """Quickly add points to all staff - one by one"""
    conn = get_db_connection()
    if not conn:
        embed = discord.Embed(
            title="❌ Database Error",
            description="Cannot connect to database",
            color=discord.Color.red()
        )
        await ctx.send(embed=embed)
        return

    try:
        cur = conn.cursor()
        cur.execute("SELECT staff_id, name, weekly_points FROM staff ORDER BY name")
        staff_list = cur.fetchall()

        if not staff_list:
            embed = discord.Embed(
                title="❌ Error",
                description="No staff members tracked!",
                color=discord.Color.red()
            )
            await ctx.send(embed=embed)
            return
    except Exception as e:
        embed = discord.Embed(
            title="❌ Error",
            description=f"Error: {str(e)}",
            color=discord.Color.red()
        )
        await ctx.send(embed=embed)
        return
    finally:
        return_db_connection(conn)

    embed = discord.Embed(
        title="📝 Batch Points Entry",
        description="Type the number of points for each staff member.\nType 'skip' to skip or 'done' to finish.\n\n⏱️ You have 30 seconds per staff member",
        color=discord.Color.blue()
    )
    await ctx.send(embed=embed)

    await asyncio.sleep(2)

    for i, (staff_id, name, current) in enumerate(staff_list, 1):
        embed = discord.Embed(
            title=f"#{i} - {name}",
            description=f"📊 Current Points: **{current}**\n\nEnter points to add (or 'skip'):",
            color=discord.Color.blue()
        )
        await ctx.send(embed=embed)

        try:
            msg = await bot.wait_for(
                'message',
                timeout=30,
                check=lambda m: m.author == ctx.author and m.channel == ctx.channel
            )

            if msg.content.lower() == 'done':
                embed = discord.Embed(
                    title="✅ Batch Complete",
                    description=f"Finished at {name}. Use !activity to see results",
                    color=discord.Color.green()
                )
                await ctx.send(embed=embed)
                break

            if msg.content.lower() == 'skip':
                embed = discord.Embed(
                    description=f"⏭️ Skipped {name}",
                    color=discord.Color.orange()
                )
                await ctx.send(embed=embed, delete_after=2)
                continue

            points = int(msg.content)
            if points < 0:
                embed = discord.Embed(
                    title="❌ Invalid",
                    description="Points must be positive!",
                    color=discord.Color.red()
                )
                await ctx.send(embed=embed, delete_after=2)
                continue

            conn = get_db_connection()
            if conn:
                cur = conn.cursor()
                new_total = current + points
                cur.execute(
                    "UPDATE staff SET weekly_points = %s, updated_at = CURRENT_TIMESTAMP WHERE staff_id = %s",
                    (new_total, staff_id)
                )
                conn.commit()
                return_db_connection(conn)

                rating = get_rating(new_total)

                embed = discord.Embed(
                    title="✅ Added",
                    description=f"**+{points}** pts added\n📊 New Total: **{new_total}** {rating}",
                    color=discord.Color.green()
                )
                await ctx.send(embed=embed, delete_after=3)

        except ValueError:
            embed = discord.Embed(
                title="❌ Invalid Input",
                description="Please enter a valid number! (or 'skip'/'done')",
                color=discord.Color.red()
            )
            await ctx.send(embed=embed, delete_after=3)
        except asyncio.TimeoutError:
            embed = discord.Embed(
                title="⏰ Timeout",
                description=f"No response for {name} - skipping",
                color=discord.Color.orange()
            )
            await ctx.send(embed=embed, delete_after=2)

    embed = discord.Embed(
        title="✅ Batch Entry Complete!",
        description="Use **!activity** to see the full leaderboard",
        color=discord.Color.green()
    )
    await ctx.send(embed=embed)

@bot.command(name='batch_set')
@commands.has_permissions(administrator=True)
async def batch_set(ctx):
    """Set exact points for all staff (end of week)"""
    conn = get_db_connection()
    if not conn:
        embed = discord.Embed(
            title="❌ Database Error",
            description="Cannot connect to database",
            color=discord.Color.red()
        )
        await ctx.send(embed=embed)
        return

    try:
        cur = conn.cursor()
        cur.execute("SELECT staff_id, name, weekly_points FROM staff ORDER BY name")
        staff_list = cur.fetchall()

        if not staff_list:
            embed = discord.Embed(
                title="❌ Error",
                description="No staff members tracked!",
                color=discord.Color.red()
            )
            await ctx.send(embed=embed)
            return
    except Exception as e:
        embed = discord.Embed(
            title="❌ Error",
            description=f"Error: {str(e)}",
            color=discord.Color.red()
        )
        await ctx.send(embed=embed)
        return
    finally:
        return_db_connection(conn)

    embed = discord.Embed(
        title="📝 Batch Set Points (End of Week)",
        description="Set the EXACT points for each staff member.\nType the number or 'skip' to keep current.\nType 'done' to finish.\n\n⏱️ You have 30 seconds per staff member",
        color=discord.Color.blue()
    )
    await ctx.send(embed=embed)

    await asyncio.sleep(2)

    for i, (staff_id, name, current) in enumerate(staff_list, 1):
        embed = discord.Embed(
            title=f"#{i} - {name}",
            description=f"📊 Current Points: **{current}**\n\nEnter EXACT points (or 'skip' to keep current):",
            color=discord.Color.blue()
        )
        await ctx.send(embed=embed)

        try:
            msg = await bot.wait_for(
                'message',
                timeout=30,
                check=lambda m: m.author == ctx.author and m.channel == ctx.channel
            )

            if msg.content.lower() == 'done':
                embed = discord.Embed(
                    title="✅ Batch Complete",
                    description=f"Finished at {name}. Use !activity to see results",
                    color=discord.Color.green()
                )
                await ctx.send(embed=embed)
                break

            if msg.content.lower() == 'skip':
                embed = discord.Embed(
                    description=f"⏭️ Kept {name} at {current} pts",
                    color=discord.Color.orange()
                )
                await ctx.send(embed=embed, delete_after=2)
                continue

            points = int(msg.content)
            if points < 0:
                embed = discord.Embed(
                    title="❌ Invalid",
                    description="Points must be positive!",
                    color=discord.Color.red()
                )
                await ctx.send(embed=embed, delete_after=2)
                continue

            conn = get_db_connection()
            if conn:
                cur = conn.cursor()
                cur.execute(
                    "UPDATE staff SET weekly_points = %s, updated_at = CURRENT_TIMESTAMP WHERE staff_id = %s",
                    (points, staff_id)
                )
                conn.commit()
                return_db_connection(conn)

                rating = get_rating(points)

                embed = discord.Embed(
                    title="✅ Updated",
                    description=f"📊 Points set to: **{points}** {rating}",
                    color=discord.Color.green()
                )
                await ctx.send(embed=embed, delete_after=3)

        except ValueError:
            embed = discord.Embed(
                title="❌ Invalid Input",
                description="Please enter a valid number! (or 'skip'/'done')",
                color=discord.Color.red()
            )
            await ctx.send(embed=embed, delete_after=3)
        except asyncio.TimeoutError:
            embed = discord.Embed(
                title="⏰ Timeout",
                description=f"No response for {name} - skipping",
                color=discord.Color.orange()
            )
            await ctx.send(embed=embed, delete_after=2)

    embed = discord.Embed(
        title="✅ Batch Entry Complete!",
        description="Use **!activity** to see the full leaderboard",
        color=discord.Color.green()
    )
    await ctx.send(embed=embed)

@bot.command(name='activity')
async def activity(ctx):
    """Display current week activity report"""
    embed = create_activity_embed()
    await ctx.send(embed=embed)

@bot.command(name='staff_info')
async def staff_info(ctx, member: discord.Member):
    """Get detailed info for a staff member"""
    staff_id = str(member.id)

    conn = get_db_connection()
    if not conn:
        embed = discord.Embed(
            title="❌ Database Error",
            description="Cannot connect to database",
            color=discord.Color.red()
        )
        await ctx.send(embed=embed)
        return

    try:
        cur = conn.cursor()
        cur.execute("SELECT name, weekly_points FROM staff WHERE staff_id = %s", (staff_id,))
        result = cur.fetchone()

        if not result:
            embed = discord.Embed(
                title="❌ Error",
                description=f"{member.display_name} is not being tracked!",
                color=discord.Color.red()
            )
            await ctx.send(embed=embed)
            return

        name, weekly_points = result
        rating = get_rating(weekly_points)

        embed = discord.Embed(
            title=f"📋 Staff Info - {name}",
            color=discord.Color.blue()
        )
        embed.add_field(name="Weekly Points", value=weekly_points, inline=True)
        embed.add_field(name="Rating", value=rating, inline=True)
        embed.add_field(name="Status", value="Active ✅" if weekly_points >= 1700 else "Needs Improvement ⚠️", inline=False)

        await ctx.send(embed=embed)
    except Exception as e:
        embed = discord.Embed(
            title="❌ Error",
            description=f"Error: {str(e)}",
            color=discord.Color.red()
        )
        await ctx.send(embed=embed)
    finally:
        return_db_connection(conn)

@bot.command(name='reset_weekly')
@commands.has_permissions(administrator=True)
async def reset_weekly(ctx):
    """Reset all weekly points (for end of week)"""
    conn = get_db_connection()
    if not conn:
        embed = discord.Embed(
            title="❌ Database Error",
            description="Cannot connect to database",
            color=discord.Color.red()
        )
        await ctx.send(embed=embed)
        return

    try:
        cur = conn.cursor()

        # Add to total and reset weekly
        cur.execute("""
            UPDATE staff
            SET total_points = total_points + weekly_points,
                weekly_points = 0,
                updated_at = CURRENT_TIMESTAMP
        """)

        # Log the reset
        cur.execute("INSERT INTO weekly_resets (reset_date) VALUES (CURRENT_TIMESTAMP)")

        conn.commit()

        embed = discord.Embed(
            title="🔄 Weekly Reset",
            description="All weekly points have been reset!\nA new week tracking period has started.",
            color=discord.Color.gold()
        )
        await ctx.send(embed=embed)
    except Exception as e:
        embed = discord.Embed(
            title="❌ Error",
            description=f"Error: {str(e)}",
            color=discord.Color.red()
        )
        await ctx.send(embed=embed)
        conn.rollback()
    finally:
        return_db_connection(conn)

@bot.command(name='remove_staff')
@commands.has_permissions(administrator=True)
async def remove_staff(ctx, member: discord.Member):
    """Remove a staff member from tracking"""
    staff_id = str(member.id)

    conn = get_db_connection()
    if not conn:
        embed = discord.Embed(
            title="❌ Database Error",
            description="Cannot connect to database",
            color=discord.Color.red()
        )
        await ctx.send(embed=embed)
        return

    try:
        cur = conn.cursor()

        # Get name before deleting
        cur.execute("SELECT name FROM staff WHERE staff_id = %s", (staff_id,))
        result = cur.fetchone()

        if not result:
            embed = discord.Embed(
                title="❌ Error",
                description=f"{member.display_name} is not being tracked!",
                color=discord.Color.red()
            )
            await ctx.send(embed=embed)
            return

        name = result[0]

        # Delete
        cur.execute("DELETE FROM staff WHERE staff_id = %s", (staff_id,))
        conn.commit()

        embed = discord.Embed(
            title="✅ Staff Removed",
            description=f"Removed **{name}** from activity tracking!",
            color=discord.Color.red()
        )
        await ctx.send(embed=embed)
    except Exception as e:
        embed = discord.Embed(
            title="❌ Error",
            description=f"Error: {str(e)}",
            color=discord.Color.red()
        )
        await ctx.send(embed=embed)
        conn.rollback()
    finally:
        return_db_connection(conn)

@bot.command(name='help')
async def help_command(ctx):
    """Display all available commands"""
    embed = discord.Embed(
        title="📖 Staff Activity Bot Commands",
        color=discord.Color.blurple()
    )

    commands_text = """
**🚀 QUICK ENTRY (Recommended for multiple staff):**
`!bulk_set_weekly` - Paste a whole weekly list at once (uses Points, ignores Available)
`!batch_add` - Add points to ALL staff one-by-one
`!batch_set` - Set exact points for ALL staff (end of week)

**Admin Commands (Administrator only):**
`!add_staff @member [name]` - Add a staff member to track
`!add_staff_manual` - Add staff from other servers (interactive)
`!add_points @member [points]` - Add points to ONE staff
`!set_points @member [points]` - Set exact points for ONE staff
`!remove_staff @member` - Remove a staff member from tracking
`!reset_weekly` - Reset all weekly points (use at end of week)

**User Commands (Everyone can use):**
`!activity` - Show current weekly activity report
`!staff_info @member` - Get detailed info about a staff member
`!help` - Show this help message

**Rating System:**
❌ REMOVE: Less than 1000 points
⚠️ BAD: 1000-1699 points
📊 OKAY: 1700-1999 points
✅ GOOD: 2000+ points
    """

    embed.description = commands_text
    embed.set_footer(text="Using PostgreSQL Database • Railway Hosted")
    await ctx.send(embed=embed)

@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        embed = discord.Embed(
            title="❌ Permission Denied",
            description="You need administrator permissions to use this command!",
            color=discord.Color.red()
        )
        await ctx.send(embed=embed)
    elif isinstance(error, commands.BadArgument):
        embed = discord.Embed(
            title="❌ Invalid Arguments",
            description="Please check your command arguments and try again!",
            color=discord.Color.red()
        )
        await ctx.send(embed=embed)
    else:
        print(f"Error: {error}")

if __name__ == "__main__":
    # Initialize database
    if not init_db_pool():
        print("❌ Failed to initialize database")
        exit(1)

    if not create_tables():
        print("❌ Failed to create tables")
        exit(1)

    # Run the bot
    if not DISCORD_TOKEN:
        print("❌ DISCORD_TOKEN not found in .env file")
        exit(1)

    bot.run(DISCORD_TOKEN)
