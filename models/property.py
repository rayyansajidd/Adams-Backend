from sqlalchemy import Column, Integer, String, Boolean, TIMESTAMP, text, ForeignKey
from sqlalchemy.orm import relationship
from db.init import Base

class Property(Base):
    __tablename__ = "properties"
    id = Column(Integer, primary_key=True, index=True)
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=False)
    nickname = Column(String(100), nullable=True) # e.g. "Main Home", "Rental"
    
    # Address Details
    address = Column(String)
    city = Column(String)
    state = Column(String)
    zip_code = Column(String)
    
    # Square Subscription Specifics per Property
    square_subscription_id = Column(String(255), nullable=True)
    subscription_active = Column(Boolean, default=False)
    subscription_status = Column(String(50), nullable=True) # ACTIVE, PAUSED, CANCELED
    plan_id = Column(String(50), nullable=True)
    plan_variation_id = Column(String(255), nullable=True)
    
    created_at = Column(TIMESTAMP, server_default=text("NOW()"))
    
    # Relationships
    customer = relationship("Customer", back_populates="properties")
