from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user
from app.db import models
from app.db.session import get_db
from app.trips.schemas import TripCreate
from app.trips.service import (
    create_trip_record,
    get_owned_trip,
    serialize_trip,
)


router = APIRouter(prefix="/trips", tags=["trips"])


@router.post("", status_code=status.HTTP_201_CREATED)
def create_trip(
    trip_input: TripCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    new_trip = create_trip_record(db, current_user.id, trip_input)
    db.commit()
    db.refresh(new_trip)

    return {
        "id": new_trip.id,
        "user_id": new_trip.user_id,
        "destination": new_trip.destination,
        "start_date": new_trip.start_date,
        "end_date": new_trip.end_date,
        "budget": new_trip.budget,
        "people": new_trip.people,
        "interests": new_trip.interests,
        "pace": new_trip.pace,
        "notes": new_trip.notes,
        "status": new_trip.status,
    }


@router.get("/{trip_id}")
def get_trip(
    trip_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    trip = get_owned_trip(db, trip_id, current_user.id)
    if trip is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Trip not found",
        )

    return serialize_trip(trip, db)


@router.get("")
def list_trips(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    trips = db.query(models.Trip).filter(
        models.Trip.user_id == current_user.id
    ).all()

    return [
        {
            "id": trip.id,
            "destination": trip.destination,
            "start_date": trip.start_date,
            "end_date": trip.end_date,
            "budget": trip.budget,
            "people": trip.people,
            "status": trip.status,
        }
        for trip in trips
    ]


@router.delete("/{trip_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_trip(
    trip_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    trip = get_owned_trip(db, trip_id, current_user.id)
    if trip is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Trip not found",
        )

    db.delete(trip)
    db.commit()
