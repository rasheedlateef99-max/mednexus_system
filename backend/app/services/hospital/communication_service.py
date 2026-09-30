"""
Internal Communication Service
-------------------------------------
Business logic for starting conversations, sending messages, and
tracking read state.

Direct Conversation Deduplication:
  Starting a "direct" conversation with someone you already have one
  with does NOT create a duplicate — it finds the existing conversation
  and posts the new message into it. This matches how every real
  messaging app behaves (you can't accidentally have two separate DM
  threads with the same person).

Unread Count Calculation:
  count of messages in the conversation where
  Message.created_at > ConversationParticipant.last_read_at
  for the requesting user's participant row, excluding their own
  messages (you don't have "unread" messages you sent yourself).
"""

import uuid
from datetime import datetime, timezone
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_, or_

from app.models.tenant.communication import (
    Conversation, ConversationParticipant, Message, ConversationType,
)
from app.models.tenant.hospital_user import HospitalUser
from app.schemas.hospital.communication import (
    StartDirectConversationRequest, StartDepartmentConversationRequest,
    StartGroupConversationRequest, SendMessageRequest,
    ConversationResponse, ConversationSummary, ConversationListResponse,
    MessageResponse, ParticipantResponse,
)
from app.core.exceptions import NotFoundException, ForbiddenException, ValidationException


async def _get_user_name(db: AsyncSession, user_id: uuid.UUID) -> str:
    result = await db.execute(select(HospitalUser.full_name).where(HospitalUser.id == user_id))
    row = result.first()
    return row[0] if row else ""


async def _get_participants(db: AsyncSession, conversation_id: uuid.UUID) -> list[ParticipantResponse]:
    result = await db.execute(
        select(ConversationParticipant).where(
            ConversationParticipant.conversation_id == conversation_id,
            ConversationParticipant.left_at.is_(None),
        )
    )
    rows = result.scalars().all()
    participants = []
    for p in rows:
        name = await _get_user_name(db, p.user_id)
        participants.append(
            ParticipantResponse(user_id=p.user_id, user_name=name, joined_at=p.joined_at, left_at=p.left_at)
        )
    return participants


async def _get_messages(db: AsyncSession, conversation_id: uuid.UUID, limit: int = 100) -> list[MessageResponse]:
    result = await db.execute(
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at.asc())
        .limit(limit)
    )
    rows = result.scalars().all()
    messages = []
    for m in rows:
        r = MessageResponse.model_validate(m)
        r.sender_name = await _get_user_name(db, m.sender_id)
        messages.append(r)
    return messages


async def _get_unread_count(
    db: AsyncSession, conversation_id: uuid.UUID, user_id: uuid.UUID, last_read_at: datetime,
) -> int:
    result = await db.execute(
        select(func.count(Message.id)).where(
            Message.conversation_id == conversation_id,
            Message.created_at > last_read_at,
            Message.sender_id != user_id,
        )
    )
    return result.scalar() or 0


async def _build_conversation_response(
    db: AsyncSession, conversation: Conversation, requesting_user_id: uuid.UUID,
) -> ConversationResponse:
    response = ConversationResponse.model_validate(conversation)
    response.participants = await _get_participants(db, conversation.id)
    response.messages = await _get_messages(db, conversation.id)

    participant_result = await db.execute(
        select(ConversationParticipant).where(
            ConversationParticipant.conversation_id == conversation.id,
            ConversationParticipant.user_id == requesting_user_id,
        )
    )
    participant = participant_result.scalar_one_or_none()
    if participant:
        response.unread_count = await _get_unread_count(
            db, conversation.id, requesting_user_id, participant.last_read_at,
        )
    return response


async def _verify_participant(
    db: AsyncSession, conversation_id: uuid.UUID, user_id: uuid.UUID,
) -> ConversationParticipant:
    result = await db.execute(
        select(ConversationParticipant).where(
            ConversationParticipant.conversation_id == conversation_id,
            ConversationParticipant.user_id == user_id,
            ConversationParticipant.left_at.is_(None),
        )
    )
    participant = result.scalar_one_or_none()
    if not participant:
        raise ForbiddenException("You are not a participant in this conversation.")
    return participant


# ── Core Service Functions ────────────────────────────────────────────────────

async def start_direct_conversation(
    db: AsyncSession, hospital_id: uuid.UUID, sender_id: uuid.UUID,
    data: StartDirectConversationRequest,
) -> ConversationResponse:
    if data.recipient_id == sender_id:
        raise ValidationException("Cannot start a conversation with yourself.")

    recipient_result = await db.execute(
        select(HospitalUser).where(
            HospitalUser.id == data.recipient_id, HospitalUser.hospital_id == hospital_id,
        )
    )
    if not recipient_result.scalar_one_or_none():
        raise NotFoundException("Recipient")

    # Deduplication: find an existing direct conversation between these two
    existing_result = await db.execute(
        select(Conversation.id)
        .join(ConversationParticipant, Conversation.id == ConversationParticipant.conversation_id)
        .where(
            Conversation.hospital_id == hospital_id,
            Conversation.conversation_type == ConversationType.DIRECT,
            ConversationParticipant.user_id.in_([sender_id, data.recipient_id]),
        )
        .group_by(Conversation.id)
        .having(func.count(ConversationParticipant.user_id.distinct()) == 2)
    )
    existing_id = existing_result.scalar()

    now = datetime.now(timezone.utc)

    if existing_id:
        conversation_result = await db.execute(select(Conversation).where(Conversation.id == existing_id))
        conversation = conversation_result.scalar_one()
    else:
        conversation = Conversation(
            hospital_id=hospital_id,
            conversation_type=ConversationType.DIRECT,
            created_by=sender_id,
            last_message_at=now,
        )
        db.add(conversation)
        await db.flush()

        db.add(ConversationParticipant(conversation_id=conversation.id, user_id=sender_id))
        db.add(ConversationParticipant(conversation_id=conversation.id, user_id=data.recipient_id))

    message = Message(conversation_id=conversation.id, sender_id=sender_id, body=data.initial_message)
    db.add(message)
    conversation.last_message_at = now

    await db.commit()
    await db.refresh(conversation)

    return await _build_conversation_response(db, conversation, sender_id)


async def start_department_conversation(
    db: AsyncSession, hospital_id: uuid.UUID, creating_user_id: uuid.UUID,
    data: StartDepartmentConversationRequest,
) -> ConversationResponse:
    staff_result = await db.execute(
        select(HospitalUser.id).where(
            HospitalUser.hospital_id == hospital_id,
            HospitalUser.department_id == data.department_id,
        )
    )
    staff_ids = [row[0] for row in staff_result.all()]
    if not staff_ids:
        raise NotFoundException("Department with active staff")

    now = datetime.now(timezone.utc)
    conversation = Conversation(
        hospital_id=hospital_id,
        conversation_type=ConversationType.DEPARTMENT,
        title=data.title,
        department_id=data.department_id,
        created_by=creating_user_id,
        last_message_at=now,
    )
    db.add(conversation)
    await db.flush()

    for staff_id in staff_ids:
        db.add(ConversationParticipant(conversation_id=conversation.id, user_id=staff_id))

    message = Message(conversation_id=conversation.id, sender_id=creating_user_id, body=data.initial_message)
    db.add(message)

    await db.commit()
    await db.refresh(conversation)

    return await _build_conversation_response(db, conversation, creating_user_id)


async def start_group_conversation(
    db: AsyncSession, hospital_id: uuid.UUID, creating_user_id: uuid.UUID,
    data: StartGroupConversationRequest,
) -> ConversationResponse:
    all_participant_ids = set(data.participant_ids) | {creating_user_id}

    staff_result = await db.execute(
        select(HospitalUser.id).where(
            HospitalUser.hospital_id == hospital_id,
            HospitalUser.id.in_(all_participant_ids),
        )
    )
    found_ids = {row[0] for row in staff_result.all()}
    missing = all_participant_ids - found_ids
    if missing:
        raise NotFoundException(f"{len(missing)} participant(s) not found in this hospital")

    now = datetime.now(timezone.utc)
    conversation = Conversation(
        hospital_id=hospital_id,
        conversation_type=ConversationType.GROUP,
        title=data.title,
        created_by=creating_user_id,
        last_message_at=now,
    )
    db.add(conversation)
    await db.flush()

    for pid in all_participant_ids:
        db.add(ConversationParticipant(conversation_id=conversation.id, user_id=pid))

    message = Message(conversation_id=conversation.id, sender_id=creating_user_id, body=data.initial_message)
    db.add(message)

    await db.commit()
    await db.refresh(conversation)

    return await _build_conversation_response(db, conversation, creating_user_id)


async def send_message(
    db: AsyncSession, hospital_id: uuid.UUID, conversation_id: uuid.UUID,
    sender_id: uuid.UUID, data: SendMessageRequest,
) -> ConversationResponse:
    conv_result = await db.execute(
        select(Conversation).where(Conversation.id == conversation_id, Conversation.hospital_id == hospital_id)
    )
    conversation = conv_result.scalar_one_or_none()
    if not conversation:
        raise NotFoundException("Conversation")

    await _verify_participant(db, conversation_id, sender_id)

    now = datetime.now(timezone.utc)
    message = Message(conversation_id=conversation_id, sender_id=sender_id, body=data.body)
    db.add(message)
    conversation.last_message_at = now

    await db.commit()
    await db.refresh(conversation)

    return await _build_conversation_response(db, conversation, sender_id)


async def get_conversation(
    db: AsyncSession, hospital_id: uuid.UUID, conversation_id: uuid.UUID, requesting_user_id: uuid.UUID,
) -> ConversationResponse:
    conv_result = await db.execute(
        select(Conversation).where(Conversation.id == conversation_id, Conversation.hospital_id == hospital_id)
    )
    conversation = conv_result.scalar_one_or_none()
    if not conversation:
        raise NotFoundException("Conversation")

    await _verify_participant(db, conversation_id, requesting_user_id)
    return await _build_conversation_response(db, conversation, requesting_user_id)


async def mark_conversation_read(
    db: AsyncSession, conversation_id: uuid.UUID, user_id: uuid.UUID,
) -> dict:
    participant = await _verify_participant(db, conversation_id, user_id)
    participant.last_read_at = datetime.now(timezone.utc)
    await db.commit()
    return {"message": "Marked as read."}


async def leave_conversation(
    db: AsyncSession, conversation_id: uuid.UUID, user_id: uuid.UUID,
) -> dict:
    participant = await _verify_participant(db, conversation_id, user_id)
    participant.left_at = datetime.now(timezone.utc)
    await db.commit()
    return {"message": "Left the conversation."}


async def list_my_conversations(
    db: AsyncSession, hospital_id: uuid.UUID, user_id: uuid.UUID, page: int = 1, per_page: int = 20,
) -> ConversationListResponse:
    query = (
        select(Conversation, ConversationParticipant)
        .join(ConversationParticipant, Conversation.id == ConversationParticipant.conversation_id)
        .where(
            Conversation.hospital_id == hospital_id,
            ConversationParticipant.user_id == user_id,
            ConversationParticipant.left_at.is_(None),
            Conversation.is_archived == False,
        )
        .order_by(Conversation.last_message_at.desc())
    )

    count_result = await db.execute(select(func.count()).select_from(query.subquery()))
    total = count_result.scalar() or 0

    offset = (page - 1) * per_page
    result = await db.execute(query.offset(offset).limit(per_page))
    rows = result.all()

    items = []
    for conversation, participant in rows:
        unread = await _get_unread_count(db, conversation.id, user_id, participant.last_read_at)

        last_msg_result = await db.execute(
            select(Message.body)
            .where(Message.conversation_id == conversation.id)
            .order_by(Message.created_at.desc())
            .limit(1)
        )
        last_msg_row = last_msg_result.first()
        preview = (last_msg_row[0][:80] if last_msg_row else "")

        participant_names = []
        all_participants = await _get_participants(db, conversation.id)
        for p in all_participants:
            if p.user_id != user_id:
                participant_names.append(p.user_name)

        items.append(
            ConversationSummary(
                id=conversation.id,
                conversation_type=conversation.conversation_type.value,
                title=conversation.title,
                last_message_at=conversation.last_message_at,
                last_message_preview=preview,
                unread_count=unread,
                participant_names=participant_names,
            )
        )

    pages = (total + per_page - 1) // per_page if total > 0 else 1
    return ConversationListResponse(items=items, total=total, page=page, per_page=per_page, pages=pages)