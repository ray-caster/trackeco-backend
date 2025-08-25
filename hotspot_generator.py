import logging
from datetime import datetime, timedelta
from sklearn.cluster import DBSCAN, KMeans, AgglomerativeClustering
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import silhouette_score
import numpy as np
from models import db, Disposal, Hotspot

logger = logging.getLogger(__name__)

def generate_hotspots():
    """
    Generate litter hotspots using advanced clustering algorithms
    Analyzes disposals from the last 7 days with multiple clustering methods
    """
    try:
        # Get disposals from last 7 days
        seven_days_ago = datetime.utcnow() - timedelta(days=7)
        recent_disposals = Disposal.query.filter(
            Disposal.timestamp >= seven_days_ago
        ).all()
        
        if len(recent_disposals) < 3:  # Need minimum data points for clustering
            logger.info("Not enough disposal data for hotspot generation")
            return
        
        # Prepare data for clustering
        coordinates = []
        points_values = []
        timestamps = []
        
        for disposal in recent_disposals:
            coordinates.append([float(disposal.latitude), float(disposal.longitude)])
            points_values.append(disposal.points_awarded)
            timestamps.append(disposal.timestamp.timestamp())
        
        coordinates = np.array(coordinates)
        points_values = np.array(points_values)
        timestamps = np.array(timestamps)
        
        # Use advanced clustering with multiple algorithms
        hotspots_created = 0
        
        # Method 1: Enhanced DBSCAN with adaptive parameters
        dbscan_hotspots = _generate_dbscan_hotspots(coordinates, points_values, timestamps)
        hotspots_created += len(dbscan_hotspots)
        
        # Method 2: Density-weighted clustering for high-activity areas
        if len(recent_disposals) >= 10:
            density_hotspots = _generate_density_weighted_hotspots(coordinates, points_values, timestamps)
            hotspots_created += len(density_hotspots)
        
        # Clear existing hotspots and add new ones
        Hotspot.query.delete()
        
        all_hotspots = dbscan_hotspots + (density_hotspots if len(recent_disposals) >= 10 else [])
        
        for hotspot_data in all_hotspots:
            hotspot = Hotspot(
                latitude=hotspot_data['latitude'],
                longitude=hotspot_data['longitude'],
                intensity=hotspot_data['intensity'],
                expires_at=datetime.utcnow() + timedelta(days=7)
            )
            db.session.add(hotspot)
        
        db.session.commit()
        logger.info(f"Generated {hotspots_created} hotspots from {len(recent_disposals)} recent disposals using advanced clustering")
        
    except Exception as e:
        logger.error(f"Error generating hotspots: {str(e)}")
        db.session.rollback()
        raise

def _generate_dbscan_hotspots(coordinates, points_values, timestamps):
    """Generate hotspots using enhanced DBSCAN clustering"""
    try:
        # Adaptive eps based on data density
        eps_values = [0.0005, 0.001, 0.002]  # Different scales for urban/suburban/rural
        best_score = -1
        best_clusters = None
        best_eps = 0.001
        
        for eps in eps_values:
            dbscan = DBSCAN(eps=eps, min_samples=3)
            cluster_labels = dbscan.fit_predict(coordinates)
            
            # Skip if no clusters or all noise
            if len(set(cluster_labels)) <= 1 or all(label == -1 for label in cluster_labels):
                continue
                
            # Calculate silhouette score for cluster quality
            valid_points = cluster_labels != -1
            if np.sum(valid_points) > 1:
                score = silhouette_score(coordinates[valid_points], cluster_labels[valid_points])
                if score > best_score:
                    best_score = score
                    best_clusters = cluster_labels
                    best_eps = eps
        
        if best_clusters is None:
            # Fallback to default DBSCAN
            dbscan = DBSCAN(eps=0.001, min_samples=3)
            best_clusters = dbscan.fit_predict(coordinates)
        
        hotspots = []
        unique_labels = set(best_clusters)
        
        for label in unique_labels:
            if label == -1:  # Noise points
                continue
                
            # Get points in this cluster
            cluster_mask = best_clusters == label
            cluster_coords = coordinates[cluster_mask]
            cluster_points = points_values[cluster_mask]
            cluster_times = timestamps[cluster_mask]
            
            # Calculate weighted center (more recent disposals have higher weight)
            current_time = datetime.utcnow().timestamp()
            time_weights = np.exp(-(current_time - cluster_times) / (24 * 3600))  # Exponential decay by day
            
            weighted_lat = np.average(cluster_coords[:, 0], weights=time_weights)
            weighted_lon = np.average(cluster_coords[:, 1], weights=time_weights)
            
            # Calculate intensity with temporal and activity factors
            num_disposals = len(cluster_coords)
            total_points = np.sum(cluster_points)
            recent_activity = np.sum(time_weights)
            
            # Multi-factor intensity calculation
            density_factor = min(num_disposals / 15.0, 1.0)
            points_factor = min(total_points / 300.0, 0.5)
            recency_factor = min(recent_activity / num_disposals, 0.3)
            
            intensity = min(0.4 + density_factor * 0.4 + points_factor + recency_factor, 1.0)
            
            hotspots.append({
                'latitude': weighted_lat,
                'longitude': weighted_lon,
                'intensity': intensity
            })
        
        logger.info(f"DBSCAN generated {len(hotspots)} hotspots with eps={best_eps}")
        return hotspots
        
    except Exception as e:
        logger.error(f"Error in DBSCAN hotspot generation: {str(e)}")
        return []

def _generate_density_weighted_hotspots(coordinates, points_values, timestamps):
    """Generate hotspots using density-weighted clustering for high-activity areas"""
    try:
        # Create feature matrix with coordinates, points, and time decay
        current_time = datetime.utcnow().timestamp()
        time_decay = np.exp(-(current_time - timestamps) / (48 * 3600))  # 2-day decay
        
        # Normalize features
        scaler = StandardScaler()
        features = np.column_stack([
            coordinates,
            points_values * time_decay,  # Weight points by recency
            time_decay  # Include recency as a feature
        ])
        
        features_scaled = scaler.fit_transform(features)
        
        # Use KMeans for dense areas (find optimal k)
        max_k = min(8, len(coordinates) // 3)
        best_k = 3
        best_score = -1
        
        for k in range(2, max_k + 1):
            kmeans = KMeans(n_clusters=k, random_state=42, n_init=10)
            cluster_labels = kmeans.fit_predict(features_scaled)
            
            if len(set(cluster_labels)) > 1:
                score = silhouette_score(features_scaled, cluster_labels)
                if score > best_score:
                    best_score = score
                    best_k = k
        
        # Generate hotspots with optimal clustering
        kmeans = KMeans(n_clusters=best_k, random_state=42, n_init=10)
        cluster_labels = kmeans.fit_predict(features_scaled)
        
        hotspots = []
        
        for label in range(best_k):
            cluster_mask = cluster_labels == label
            cluster_coords = coordinates[cluster_mask]
            cluster_points = points_values[cluster_mask]
            cluster_times = timestamps[cluster_mask]
            
            # Only create hotspot if cluster has sufficient activity
            if len(cluster_coords) < 3:
                continue
            
            # Calculate activity-weighted center
            activity_weights = cluster_points * np.exp(-(current_time - cluster_times) / (24 * 3600))
            activity_weights = activity_weights / np.sum(activity_weights)
            
            center_lat = np.average(cluster_coords[:, 0], weights=activity_weights)
            center_lon = np.average(cluster_coords[:, 1], weights=activity_weights)
            
            # Calculate intensity based on cluster density and activity
            total_activity = np.sum(activity_weights * cluster_points)
            cluster_density = len(cluster_coords) / (np.std(cluster_coords, axis=0).mean() + 0.001)
            
            intensity = min(0.3 + (total_activity / 200.0) + (cluster_density / 1000.0), 1.0)
            
            # Only add high-intensity hotspots from this method
            if intensity > 0.6:
                hotspots.append({
                    'latitude': center_lat,
                    'longitude': center_lon,
                    'intensity': intensity
                })
        
        logger.info(f"Density-weighted clustering generated {len(hotspots)} high-intensity hotspots")
        return hotspots
        
    except Exception as e:
        logger.error(f"Error in density-weighted hotspot generation: {str(e)}")
        return []

def cleanup_expired_hotspots():
    """Remove expired hotspots"""
    try:
        expired_count = Hotspot.query.filter(
            Hotspot.expires_at <= datetime.utcnow()
        ).delete()
        
        db.session.commit()
        logger.info(f"Cleaned up {expired_count} expired hotspots")
        
    except Exception as e:
        logger.error(f"Error cleaning up expired hotspots: {str(e)}")
        db.session.rollback()

def generate_offline_hotspots(cached_disposals):
    """Generate hotspots from cached offline data"""
    try:
        if len(cached_disposals) < 3:
            return []
        
        coordinates = []
        points_values = []
        
        for disposal in cached_disposals:
            coordinates.append([disposal['latitude'], disposal['longitude']])
            points_values.append(10)  # Standard points for offline data
        
        coordinates = np.array(coordinates)
        points_values = np.array(points_values)
        timestamps = np.array([datetime.utcnow().timestamp()] * len(coordinates))
        
        # Use simple DBSCAN for offline data
        hotspots = _generate_dbscan_hotspots(coordinates, points_values, timestamps)
        
        logger.info(f"Generated {len(hotspots)} offline hotspots from {len(cached_disposals)} cached disposals")
        return hotspots
        
    except Exception as e:
        logger.error(f"Error generating offline hotspots: {str(e)}")
        return []
