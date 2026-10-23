// Endpoint catalog adapted from ScrapeCreators CLI under the MIT license.
export const apis = [
  {
    "id": "youtube",
    "name": "YouTube",
    "description": "YouTube endpoints",
    "endpoints": [
      {
        "name": "Channel Details",
        "method": "GET",
        "description": "Channel Details",
        "path": "/v1/youtube/channel",
        "params": [
          {
            "name": "channelId",
            "type": "string",
            "required": false,
            "description": "Channel id"
          },
          {
            "name": "handle",
            "type": "string",
            "required": false,
            "description": "Handle"
          },
          {
            "name": "url",
            "type": "string",
            "required": false,
            "description": "Url"
          },
          {
            "name": "cache_max_age",
            "type": "select",
            "required": false,
            "description": "Cache max age",
            "options": [
              "1d",
              "3d",
              "7d",
              "14d",
              "30d"
            ]
          }
        ]
      },
      {
        "name": "Channel Videos",
        "method": "GET",
        "description": "Channel Videos",
        "path": "/v1/youtube/channel-videos",
        "params": [
          {
            "name": "channelId",
            "type": "string",
            "required": false,
            "description": "Channel id"
          },
          {
            "name": "handle",
            "type": "string",
            "required": false,
            "description": "Handle"
          },
          {
            "name": "sort",
            "type": "select",
            "required": false,
            "description": "Sort",
            "options": [
              "latest",
              "popular"
            ]
          },
          {
            "name": "continuationToken",
            "type": "string",
            "required": false,
            "description": "Continuationtoken"
          },
          {
            "name": "is_paid_promotions",
            "type": "string",
            "required": false,
            "description": "Is paid promotions"
          },
          {
            "name": "includeExtras",
            "type": "string",
            "required": false,
            "description": "Includeextras"
          }
        ]
      },
      {
        "name": "Channel Community Posts",
        "method": "GET",
        "description": "Channel Community Posts",
        "path": "/v1/youtube/channel/community-posts",
        "params": [
          {
            "name": "channelId",
            "type": "string",
            "required": false,
            "description": "Channel id"
          },
          {
            "name": "handle",
            "type": "string",
            "required": false,
            "description": "Handle"
          },
          {
            "name": "continuationToken",
            "type": "string",
            "required": false,
            "description": "Continuationtoken"
          }
        ]
      },
      {
        "name": "Channel Lives",
        "method": "GET",
        "description": "Channel Lives",
        "path": "/v1/youtube/channel/lives",
        "params": [
          {
            "name": "channelId",
            "type": "string",
            "required": false,
            "description": "Channel id"
          },
          {
            "name": "handle",
            "type": "string",
            "required": false,
            "description": "Handle"
          },
          {
            "name": "continuationToken",
            "type": "string",
            "required": false,
            "description": "Continuationtoken"
          }
        ]
      },
      {
        "name": "Channel Playlists",
        "method": "GET",
        "description": "Channel Playlists",
        "path": "/v1/youtube/channel/playlists",
        "params": [
          {
            "name": "channelId",
            "type": "string",
            "required": false,
            "description": "Channel id"
          },
          {
            "name": "handle",
            "type": "string",
            "required": false,
            "description": "Handle"
          },
          {
            "name": "continuationToken",
            "type": "string",
            "required": false,
            "description": "Continuationtoken"
          }
        ]
      },
      {
        "name": "Channel Shorts",
        "method": "GET",
        "description": "Channel Shorts",
        "path": "/v1/youtube/channel/shorts",
        "params": [
          {
            "name": "handle",
            "type": "string",
            "required": false,
            "description": "Handle"
          },
          {
            "name": "channelId",
            "type": "string",
            "required": false,
            "description": "Channel id"
          },
          {
            "name": "sort",
            "type": "select",
            "required": false,
            "description": "Sort",
            "options": [
              "latest",
              "popular"
            ]
          },
          {
            "name": "continuationToken",
            "type": "string",
            "required": false,
            "description": "Continuationtoken"
          }
        ]
      },
      {
        "name": "Community Post Details",
        "method": "GET",
        "description": "Community Post Details",
        "path": "/v1/youtube/community-post",
        "params": [
          {
            "name": "url",
            "type": "string",
            "required": true,
            "description": "Url"
          }
        ]
      },
      {
        "name": "Playlist",
        "method": "GET",
        "description": "Playlist",
        "path": "/v1/youtube/playlist",
        "params": [
          {
            "name": "playlist_id",
            "type": "string",
            "required": true,
            "description": "Playlist id"
          },
          {
            "name": "continuationToken",
            "type": "string",
            "required": false,
            "description": "Continuationtoken"
          }
        ]
      },
      {
        "name": "Search",
        "method": "GET",
        "description": "Search",
        "path": "/v1/youtube/search",
        "params": [
          {
            "name": "query",
            "type": "string",
            "required": true,
            "description": "Query"
          },
          {
            "name": "uploadDate",
            "type": "select",
            "required": false,
            "description": "Uploaddate",
            "options": [
              "today",
              "this_week",
              "this_month",
              "this_year"
            ]
          },
          {
            "name": "sortBy",
            "type": "select",
            "required": false,
            "description": "Sortby",
            "options": [
              "relevance",
              "popular"
            ]
          },
          {
            "name": "type",
            "type": "select",
            "required": false,
            "description": "Type",
            "options": [
              "videos",
              "shorts",
              "channels",
              "playlists"
            ]
          },
          {
            "name": "duration",
            "type": "select",
            "required": false,
            "description": "Duration",
            "options": [
              "under_3_min",
              "between_3_and_20_min",
              "over_20_min"
            ]
          },
          {
            "name": "region",
            "type": "string",
            "required": false,
            "description": "Region"
          },
          {
            "name": "continuationToken",
            "type": "string",
            "required": false,
            "description": "Continuationtoken"
          },
          {
            "name": "includeExtras",
            "type": "string",
            "required": false,
            "description": "Includeextras"
          },
          {
            "name": "is_paid_promotions",
            "type": "string",
            "required": false,
            "description": "Is paid promotions"
          }
        ]
      },
      {
        "name": "Search by Hashtag",
        "method": "GET",
        "description": "Search by Hashtag",
        "path": "/v1/youtube/search/hashtag",
        "params": [
          {
            "name": "hashtag",
            "type": "string",
            "required": true,
            "description": "Hashtag"
          },
          {
            "name": "continuationToken",
            "type": "string",
            "required": false,
            "description": "Continuationtoken"
          },
          {
            "name": "type",
            "type": "select",
            "required": false,
            "description": "Type",
            "options": [
              "videos",
              "shorts",
              "channels",
              "playlists"
            ]
          }
        ]
      },
      {
        "name": "Search Typeahead",
        "method": "GET",
        "description": "Search Typeahead",
        "path": "/v1/youtube/search/typeahead",
        "params": [
          {
            "name": "query",
            "type": "string",
            "required": true,
            "description": "Query"
          }
        ]
      },
      {
        "name": "Trending Shorts",
        "method": "GET",
        "description": "Trending Shorts",
        "path": "/v1/youtube/shorts/trending",
        "params": []
      },
      {
        "name": "Video/Short Details",
        "method": "GET",
        "description": "Video/Short Details",
        "path": "/v1/youtube/video",
        "params": [
          {
            "name": "url",
            "type": "string",
            "required": true,
            "description": "Url"
          },
          {
            "name": "language",
            "type": "string",
            "required": false,
            "description": "Language"
          },
          {
            "name": "cache_max_age",
            "type": "select",
            "required": false,
            "description": "Cache max age",
            "options": [
              "1d",
              "3d",
              "7d",
              "14d",
              "30d"
            ]
          }
        ]
      },
      {
        "name": "Comment Replies",
        "method": "GET",
        "description": "Comment Replies",
        "path": "/v1/youtube/video/comment/replies",
        "params": [
          {
            "name": "continuationToken",
            "type": "string",
            "required": true,
            "description": "Continuationtoken"
          }
        ]
      },
      {
        "name": "Comments",
        "method": "GET",
        "description": "Comments",
        "path": "/v1/youtube/video/comments",
        "params": [
          {
            "name": "url",
            "type": "string",
            "required": true,
            "description": "Url"
          },
          {
            "name": "continuationToken",
            "type": "string",
            "required": false,
            "description": "Continuationtoken"
          },
          {
            "name": "order",
            "type": "select",
            "required": false,
            "description": "Order",
            "options": [
              "top",
              "newest"
            ]
          }
        ]
      },
      {
        "name": "Video Sponsors",
        "method": "GET",
        "description": "Video Sponsors",
        "path": "/v1/youtube/video/sponsors",
        "params": [
          {
            "name": "url",
            "type": "string",
            "required": true,
            "description": "Url"
          },
          {
            "name": "language",
            "type": "string",
            "required": false,
            "description": "Language"
          }
        ]
      },
      {
        "name": "Transcript",
        "method": "GET",
        "description": "Transcript",
        "path": "/v1/youtube/video/transcript",
        "params": [
          {
            "name": "url",
            "type": "string",
            "required": true,
            "description": "Url"
          },
          {
            "name": "language",
            "type": "string",
            "required": false,
            "description": "Language"
          },
          {
            "name": "original_audio",
            "type": "boolean",
            "required": false,
            "description": "Original audio"
          },
          {
            "name": "cache_max_age",
            "type": "select",
            "required": false,
            "description": "Cache max age",
            "options": [
              "1d",
              "3d",
              "7d",
              "14d",
              "30d"
            ]
          }
        ]
      }
    ]
  },
  {
    "id": "tiktok",
    "name": "TikTok",
    "description": "TikTok endpoints",
    "endpoints": [
      {
        "name": "Ad Library Ad",
        "method": "GET",
        "description": "Ad Library Ad",
        "path": "/v1/tiktok/ad-library/ad",
        "params": [
          {
            "name": "ad_id",
            "type": "string",
            "required": true,
            "description": "Ad id"
          }
        ]
      },
      {
        "name": "Ad Library Search",
        "method": "GET",
        "description": "Ad Library Search",
        "path": "/v1/tiktok/ad-library/search",
        "params": [
          {
            "name": "query",
            "type": "string",
            "required": false,
            "description": "Query"
          },
          {
            "name": "advertiser_name",
            "type": "string",
            "required": false,
            "description": "Advertiser name"
          },
          {
            "name": "adv_biz_ids",
            "type": "string",
            "required": false,
            "description": "Adv biz ids"
          },
          {
            "name": "cursor",
            "type": "string",
            "required": false,
            "description": "Cursor"
          }
        ]
      },
      {
        "name": "Collection Videos",
        "method": "GET",
        "description": "Collection Videos",
        "path": "/v1/tiktok/collection/videos",
        "params": [
          {
            "name": "url",
            "type": "string",
            "required": true,
            "description": "Url"
          },
          {
            "name": "cursor",
            "type": "string",
            "required": false,
            "description": "Cursor"
          }
        ]
      },
      {
        "name": "Get popular creators",
        "method": "GET",
        "description": "Get popular creators",
        "path": "/v1/tiktok/creators/popular",
        "params": [
          {
            "name": "page",
            "type": "string",
            "required": false,
            "description": "Page"
          },
          {
            "name": "sortBy",
            "type": "select",
            "required": false,
            "description": "Sortby",
            "options": [
              "relevance",
              "popular"
            ]
          },
          {
            "name": "followerCount",
            "type": "select",
            "required": false,
            "description": "Followercount",
            "options": [
              "ALL",
              "1K_TO_10K",
              "10K_TO_100K",
              "100K_TO_1M",
              "1M_PLUS"
            ]
          },
          {
            "name": "creatorCountry",
            "type": "string",
            "required": false,
            "description": "Creatorcountry"
          },
          {
            "name": "audienceCountry",
            "type": "string",
            "required": false,
            "description": "Audiencecountry"
          }
        ]
      },
      {
        "name": "Trending Feed",
        "method": "GET",
        "description": "Trending Feed",
        "path": "/v1/tiktok/get-trending-feed",
        "params": [
          {
            "name": "region",
            "type": "string",
            "required": true,
            "description": "Region"
          },
          {
            "name": "trim",
            "type": "boolean",
            "required": false,
            "description": "Trim"
          }
        ]
      },
      {
        "name": "Live Info",
        "method": "GET",
        "description": "Live Info",
        "path": "/v1/tiktok/live",
        "params": [
          {
            "name": "room_id",
            "type": "string",
            "required": true,
            "description": "Room id"
          },
          {
            "name": "user_id",
            "type": "string",
            "required": true,
            "description": "User id"
          }
        ]
      },
      {
        "name": "Product Details",
        "method": "GET",
        "description": "Product Details",
        "path": "/v1/tiktok/product",
        "params": [
          {
            "name": "url",
            "type": "string",
            "required": true,
            "description": "Url"
          },
          {
            "name": "region",
            "type": "string",
            "required": false,
            "description": "Region"
          }
        ]
      },
      {
        "name": "Profile",
        "method": "GET",
        "description": "Profile",
        "path": "/v1/tiktok/profile",
        "params": [
          {
            "name": "handle",
            "type": "string",
            "required": false,
            "description": "Handle"
          },
          {
            "name": "user_id",
            "type": "string",
            "required": false,
            "description": "User id"
          },
          {
            "name": "cache_max_age",
            "type": "select",
            "required": false,
            "description": "Cache max age",
            "options": [
              "1d",
              "3d",
              "7d",
              "14d",
              "30d"
            ]
          }
        ]
      },
      {
        "name": "Profile Region",
        "method": "GET",
        "description": "Profile Region",
        "path": "/v1/tiktok/profile/region",
        "params": [
          {
            "name": "handle",
            "type": "string",
            "required": true,
            "description": "Handle"
          }
        ]
      },
      {
        "name": "Search by Hashtag",
        "method": "GET",
        "description": "Search by Hashtag",
        "path": "/v1/tiktok/search/hashtag",
        "params": [
          {
            "name": "hashtag",
            "type": "string",
            "required": true,
            "description": "Hashtag"
          },
          {
            "name": "region",
            "type": "string",
            "required": false,
            "description": "Region"
          },
          {
            "name": "cursor",
            "type": "string",
            "required": false,
            "description": "Cursor"
          },
          {
            "name": "trim",
            "type": "boolean",
            "required": false,
            "description": "Trim"
          }
        ]
      },
      {
        "name": "Search by Keyword",
        "method": "GET",
        "description": "Search by Keyword",
        "path": "/v1/tiktok/search/keyword",
        "params": [
          {
            "name": "query",
            "type": "string",
            "required": true,
            "description": "Query"
          },
          {
            "name": "date_posted",
            "type": "select",
            "required": false,
            "description": "Date posted",
            "options": [
              "all",
              "yesterday",
              "this_week",
              "this_month",
              "last_3_months",
              "last_6_months"
            ]
          },
          {
            "name": "sort_by",
            "type": "select",
            "required": false,
            "description": "Sort by",
            "options": [
              "relevance",
              "like_count",
              "date_posted"
            ]
          },
          {
            "name": "region",
            "type": "string",
            "required": false,
            "description": "Region"
          },
          {
            "name": "cursor",
            "type": "string",
            "required": false,
            "description": "Cursor"
          },
          {
            "name": "trim",
            "type": "boolean",
            "required": false,
            "description": "Trim"
          }
        ]
      },
      {
        "name": "Search Suggestions",
        "method": "GET",
        "description": "Search Suggestions",
        "path": "/v1/tiktok/search/suggestions",
        "params": [
          {
            "name": "query",
            "type": "string",
            "required": true,
            "description": "Query"
          },
          {
            "name": "region",
            "type": "string",
            "required": false,
            "description": "Region"
          }
        ]
      },
      {
        "name": "Top Search",
        "method": "GET",
        "description": "Top Search",
        "path": "/v1/tiktok/search/top",
        "params": [
          {
            "name": "query",
            "type": "string",
            "required": true,
            "description": "Query"
          },
          {
            "name": "publish_time",
            "type": "select",
            "required": false,
            "description": "Publish time",
            "options": [
              "0",
              "1",
              "7",
              "30",
              "90",
              "180"
            ]
          },
          {
            "name": "sort_by",
            "type": "select",
            "required": false,
            "description": "Sort by",
            "options": [
              "relevance",
              "like_count",
              "date_posted"
            ]
          },
          {
            "name": "region",
            "type": "string",
            "required": false,
            "description": "Region"
          },
          {
            "name": "cursor",
            "type": "string",
            "required": false,
            "description": "Cursor"
          }
        ]
      },
      {
        "name": "Search Users",
        "method": "GET",
        "description": "Search Users",
        "path": "/v1/tiktok/search/users",
        "params": [
          {
            "name": "query",
            "type": "string",
            "required": true,
            "description": "Query"
          },
          {
            "name": "cursor",
            "type": "string",
            "required": false,
            "description": "Cursor"
          },
          {
            "name": "trim",
            "type": "boolean",
            "required": false,
            "description": "Trim"
          }
        ]
      },
      {
        "name": "Product Reviews",
        "method": "GET",
        "description": "Product Reviews",
        "path": "/v1/tiktok/shop/product/reviews",
        "params": [
          {
            "name": "url",
            "type": "string",
            "required": false,
            "description": "Url"
          },
          {
            "name": "product_id",
            "type": "string",
            "required": false,
            "description": "Product id"
          },
          {
            "name": "region",
            "type": "string",
            "required": false,
            "description": "Region"
          },
          {
            "name": "page",
            "type": "string",
            "required": false,
            "description": "Page"
          }
        ]
      },
      {
        "name": "Shop Products",
        "method": "GET",
        "description": "Shop Products",
        "path": "/v1/tiktok/shop/products",
        "params": [
          {
            "name": "url",
            "type": "string",
            "required": true,
            "description": "Url"
          },
          {
            "name": "cursor",
            "type": "string",
            "required": false,
            "description": "Cursor"
          },
          {
            "name": "sort_by",
            "type": "select",
            "required": false,
            "description": "Sort by",
            "options": [
              "relevance",
              "like_count",
              "date_posted"
            ]
          },
          {
            "name": "region",
            "type": "string",
            "required": false,
            "description": "Region"
          }
        ]
      },
      {
        "name": "Shop Search",
        "method": "GET",
        "description": "Shop Search",
        "path": "/v1/tiktok/shop/search",
        "params": [
          {
            "name": "query",
            "type": "string",
            "required": true,
            "description": "Query"
          },
          {
            "name": "page",
            "type": "string",
            "required": false,
            "description": "Page"
          },
          {
            "name": "region",
            "type": "string",
            "required": false,
            "description": "Region"
          }
        ]
      },
      {
        "name": "Get Song Details",
        "method": "GET",
        "description": "Get Song Details",
        "path": "/v1/tiktok/song",
        "params": [
          {
            "name": "clipId",
            "type": "string",
            "required": true,
            "description": "Clip id"
          }
        ]
      },
      {
        "name": "TikToks using Song",
        "method": "GET",
        "description": "TikToks using Song",
        "path": "/v1/tiktok/song/videos",
        "params": [
          {
            "name": "clipId",
            "type": "string",
            "required": false,
            "description": "Clip id"
          },
          {
            "name": "cursor",
            "type": "string",
            "required": false,
            "description": "Cursor"
          }
        ]
      },
      {
        "name": "User's Audience Demographics",
        "method": "GET",
        "description": "User's Audience Demographics",
        "path": "/v1/tiktok/user/audience",
        "params": [
          {
            "name": "handle",
            "type": "string",
            "required": true,
            "description": "Handle"
          }
        ]
      },
      {
        "name": "Followers",
        "method": "GET",
        "description": "Followers",
        "path": "/v1/tiktok/user/followers",
        "params": [
          {
            "name": "handle",
            "type": "string",
            "required": false,
            "description": "Handle"
          },
          {
            "name": "user_id",
            "type": "string",
            "required": false,
            "description": "User id"
          },
          {
            "name": "min_time",
            "type": "string",
            "required": false,
            "description": "Min time"
          },
          {
            "name": "trim",
            "type": "boolean",
            "required": false,
            "description": "Trim"
          }
        ]
      },
      {
        "name": "Following",
        "method": "GET",
        "description": "Following",
        "path": "/v1/tiktok/user/following",
        "params": [
          {
            "name": "handle",
            "type": "string",
            "required": true,
            "description": "Handle"
          },
          {
            "name": "min_time",
            "type": "string",
            "required": false,
            "description": "Min time"
          },
          {
            "name": "trim",
            "type": "boolean",
            "required": false,
            "description": "Trim"
          }
        ]
      },
      {
        "name": "Live",
        "method": "GET",
        "description": "Live",
        "path": "/v1/tiktok/user/live",
        "params": [
          {
            "name": "handle",
            "type": "string",
            "required": true,
            "description": "Handle"
          }
        ]
      },
      {
        "name": "User Showcase",
        "method": "GET",
        "description": "User Showcase",
        "path": "/v1/tiktok/user/showcase",
        "params": [
          {
            "name": "handle",
            "type": "string",
            "required": true,
            "description": "Handle"
          },
          {
            "name": "region",
            "type": "string",
            "required": false,
            "description": "Region"
          },
          {
            "name": "cursor",
            "type": "string",
            "required": false,
            "description": "Cursor"
          }
        ]
      },
      {
        "name": "Comment Replies",
        "method": "GET",
        "description": "Comment Replies",
        "path": "/v1/tiktok/video/comment/replies",
        "params": [
          {
            "name": "comment_id",
            "type": "string",
            "required": true,
            "description": "Comment id"
          },
          {
            "name": "url",
            "type": "string",
            "required": true,
            "description": "Url"
          },
          {
            "name": "cursor",
            "type": "string",
            "required": false,
            "description": "Cursor"
          }
        ]
      },
      {
        "name": "Comments",
        "method": "GET",
        "description": "Comments",
        "path": "/v1/tiktok/video/comments",
        "params": [
          {
            "name": "url",
            "type": "string",
            "required": true,
            "description": "Url"
          },
          {
            "name": "cursor",
            "type": "string",
            "required": false,
            "description": "Cursor"
          },
          {
            "name": "trim",
            "type": "boolean",
            "required": false,
            "description": "Trim"
          }
        ]
      },
      {
        "name": "Transcript",
        "method": "GET",
        "description": "Transcript",
        "path": "/v1/tiktok/video/transcript",
        "params": [
          {
            "name": "allow_long_transcription",
            "type": "boolean",
            "required": false,
            "description": "Allow long transcription"
          },
          {
            "name": "url",
            "type": "string",
            "required": true,
            "description": "Url"
          },
          {
            "name": "language",
            "type": "string",
            "required": false,
            "description": "Language"
          },
          {
            "name": "use_ai_as_fallback",
            "type": "boolean",
            "required": false,
            "description": "Use ai as fallback"
          }
        ]
      },
      {
        "name": "Video Info",
        "method": "GET",
        "description": "Video Info",
        "path": "/v2/tiktok/video",
        "params": [
          {
            "name": "url",
            "type": "string",
            "required": true,
            "description": "Url"
          },
          {
            "name": "get_transcript",
            "type": "boolean",
            "required": false,
            "description": "Get transcript"
          },
          {
            "name": "region",
            "type": "string",
            "required": false,
            "description": "Region"
          },
          {
            "name": "trim",
            "type": "boolean",
            "required": false,
            "description": "Trim"
          },
          {
            "name": "download_media",
            "type": "boolean",
            "required": false,
            "description": "Download media"
          },
          {
            "name": "cache_max_age",
            "type": "select",
            "required": false,
            "description": "Cache max age",
            "options": [
              "1d",
              "3d",
              "7d",
              "14d",
              "30d"
            ]
          }
        ]
      },
      {
        "name": "Profile Videos",
        "method": "GET",
        "description": "Profile Videos",
        "path": "/v3/tiktok/profile/videos",
        "params": [
          {
            "name": "handle",
            "type": "string",
            "required": true,
            "description": "Handle"
          },
          {
            "name": "user_id",
            "type": "string",
            "required": false,
            "description": "User id"
          },
          {
            "name": "sort_by",
            "type": "select",
            "required": false,
            "description": "Sort by",
            "options": [
              "relevance",
              "like_count",
              "date_posted"
            ]
          },
          {
            "name": "max_cursor",
            "type": "string",
            "required": false,
            "description": "Max cursor"
          },
          {
            "name": "region",
            "type": "string",
            "required": false,
            "description": "Region"
          },
          {
            "name": "include_pinned",
            "type": "boolean",
            "required": false,
            "description": "Include pinned"
          },
          {
            "name": "trim",
            "type": "boolean",
            "required": false,
            "description": "Trim"
          }
        ]
      }
    ]
  },
  {
    "id": "instagram",
    "name": "Instagram",
    "description": "Instagram endpoints",
    "endpoints": [
      {
        "name": "Get Reels By Audio Id",
        "method": "GET",
        "description": "Get Reels By Audio Id",
        "path": "/v1/instagram/audio/reels",
        "params": [
          {
            "name": "audio_id",
            "type": "string",
            "required": true,
            "description": "Audio id"
          },
          {
            "name": "cursor",
            "type": "string",
            "required": false,
            "description": "Cursor"
          }
        ]
      },
      {
        "name": "Basic Profile",
        "method": "GET",
        "description": "Basic Profile",
        "path": "/v1/instagram/basic-profile",
        "params": [
          {
            "name": "userId",
            "type": "string",
            "required": false,
            "description": "User id"
          },
          {
            "name": "cache_max_age",
            "type": "select",
            "required": false,
            "description": "Cache max age",
            "options": [
              "1d",
              "3d",
              "7d",
              "14d",
              "30d"
            ]
          }
        ]
      },
      {
        "name": "Post/Reel Info",
        "method": "GET",
        "description": "Post/Reel Info",
        "path": "/v1/instagram/post",
        "params": [
          {
            "name": "url",
            "type": "string",
            "required": true,
            "description": "Url"
          },
          {
            "name": "region",
            "type": "string",
            "required": false,
            "description": "Region"
          },
          {
            "name": "trim",
            "type": "boolean",
            "required": false,
            "description": "Trim"
          },
          {
            "name": "download_media",
            "type": "boolean",
            "required": false,
            "description": "Download media"
          },
          {
            "name": "include_play_count",
            "type": "boolean",
            "required": false,
            "description": "Include play count"
          },
          {
            "name": "cache_max_age",
            "type": "select",
            "required": false,
            "description": "Cache max age",
            "options": [
              "1d",
              "3d",
              "7d",
              "14d",
              "30d"
            ]
          }
        ]
      },
      {
        "name": "Comment Replies",
        "method": "GET",
        "description": "Comment Replies",
        "path": "/v1/instagram/post/comment/replies",
        "params": [
          {
            "name": "url",
            "type": "string",
            "required": true,
            "description": "Url"
          },
          {
            "name": "comment_id",
            "type": "string",
            "required": true,
            "description": "Comment id"
          },
          {
            "name": "cursor",
            "type": "string",
            "required": false,
            "description": "Cursor"
          }
        ]
      },
      {
        "name": "Profile",
        "method": "GET",
        "description": "Profile",
        "path": "/v1/instagram/profile",
        "params": [
          {
            "name": "handle",
            "type": "string",
            "required": true,
            "description": "Handle"
          },
          {
            "name": "trim",
            "type": "boolean",
            "required": false,
            "description": "Trim"
          },
          {
            "name": "cache_max_age",
            "type": "select",
            "required": false,
            "description": "Cache max age",
            "options": [
              "1d",
              "3d",
              "7d",
              "14d",
              "30d"
            ]
          }
        ]
      },
      {
        "name": "Profile Post Count",
        "method": "GET",
        "description": "Profile Post Count",
        "path": "/v1/instagram/profile/post-count",
        "params": [
          {
            "name": "handle",
            "type": "string",
            "required": true,
            "description": "Handle"
          },
          {
            "name": "allow_estimated",
            "type": "boolean",
            "required": false,
            "description": "Allow estimated"
          }
        ]
      },
      {
        "name": "Trending Reels",
        "method": "GET",
        "description": "Trending Reels",
        "path": "/v1/instagram/reels/trending",
        "params": []
      },
      {
        "name": "Search Instagram",
        "method": "GET",
        "description": "Search Instagram",
        "path": "/v1/instagram/search",
        "params": [
          {
            "name": "query",
            "type": "string",
            "required": true,
            "description": "Query"
          }
        ]
      },
      {
        "name": "Search Hashtag Posts",
        "method": "GET",
        "description": "Search Hashtag Posts",
        "path": "/v1/instagram/search/hashtag",
        "params": [
          {
            "name": "hashtag",
            "type": "string",
            "required": true,
            "description": "Hashtag"
          },
          {
            "name": "date_posted",
            "type": "select",
            "required": false,
            "description": "Date posted",
            "options": [
              "all",
              "yesterday",
              "this_week",
              "this_month",
              "last_3_months",
              "last_6_months"
            ]
          },
          {
            "name": "media_type",
            "type": "select",
            "required": false,
            "description": "Media type",
            "options": [
              "all",
              "reels",
              "photos"
            ]
          },
          {
            "name": "cursor",
            "type": "string",
            "required": false,
            "description": "Cursor"
          }
        ]
      },
      {
        "name": "Popular Search",
        "method": "GET",
        "description": "Popular Search",
        "path": "/v1/instagram/search/popular",
        "params": [
          {
            "name": "query",
            "type": "string",
            "required": true,
            "description": "Query"
          },
          {
            "name": "cursor",
            "type": "string",
            "required": false,
            "description": "Cursor"
          }
        ]
      },
      {
        "name": "Search Instagram Profiles",
        "method": "GET",
        "description": "Search Instagram Profiles",
        "path": "/v1/instagram/search/profiles",
        "params": [
          {
            "name": "query",
            "type": "string",
            "required": true,
            "description": "Query"
          }
        ]
      },
      {
        "name": "Embed HTML",
        "method": "GET",
        "description": "Embed HTML",
        "path": "/v1/instagram/user/embed",
        "params": [
          {
            "name": "handle",
            "type": "string",
            "required": true,
            "description": "Handle"
          }
        ]
      },
      {
        "name": "Highlights Details",
        "method": "GET",
        "description": "Highlights Details",
        "path": "/v1/instagram/user/highlight/detail",
        "params": [
          {
            "name": "id",
            "type": "string",
            "required": false,
            "description": "Id"
          }
        ]
      },
      {
        "name": "Story Highlights",
        "method": "GET",
        "description": "Story Highlights",
        "path": "/v1/instagram/user/highlights",
        "params": [
          {
            "name": "user_id",
            "type": "string",
            "required": false,
            "description": "User id"
          },
          {
            "name": "handle",
            "type": "string",
            "required": false,
            "description": "Handle"
          }
        ]
      },
      {
        "name": "Reels",
        "method": "GET",
        "description": "Reels",
        "path": "/v1/instagram/user/reels",
        "params": [
          {
            "name": "user_id",
            "type": "string",
            "required": false,
            "description": "User id"
          },
          {
            "name": "handle",
            "type": "string",
            "required": false,
            "description": "Handle"
          },
          {
            "name": "max_id",
            "type": "string",
            "required": false,
            "description": "Max id"
          },
          {
            "name": "trim",
            "type": "boolean",
            "required": false,
            "description": "Trim"
          }
        ]
      },
      {
        "name": "User Tagged Posts",
        "method": "GET",
        "description": "User Tagged Posts",
        "path": "/v1/instagram/user/tagged-posts",
        "params": [
          {
            "name": "user_id",
            "type": "string",
            "required": true,
            "description": "User id"
          },
          {
            "name": "cursor",
            "type": "string",
            "required": false,
            "description": "Cursor"
          }
        ]
      },
      {
        "name": "Transcript",
        "method": "GET",
        "description": "Transcript",
        "path": "/v2/instagram/media/transcript",
        "params": [
          {
            "name": "allow_long_transcription",
            "type": "boolean",
            "required": false,
            "description": "Allow long transcription"
          },
          {
            "name": "url",
            "type": "string",
            "required": true,
            "description": "Url"
          },
          {
            "name": "cache_max_age",
            "type": "select",
            "required": false,
            "description": "Cache max age",
            "options": [
              "1d",
              "3d",
              "7d",
              "14d",
              "30d"
            ]
          }
        ]
      },
      {
        "name": "Comments",
        "method": "GET",
        "description": "Comments",
        "path": "/v2/instagram/post/comments",
        "params": [
          {
            "name": "url",
            "type": "string",
            "required": true,
            "description": "Url"
          },
          {
            "name": "cursor",
            "type": "string",
            "required": false,
            "description": "Cursor"
          },
          {
            "name": "include_replies",
            "type": "boolean",
            "required": false,
            "description": "Include replies"
          }
        ]
      },
      {
        "name": "Search Reels",
        "method": "GET",
        "description": "Search Reels",
        "path": "/v2/instagram/reels/search",
        "params": [
          {
            "name": "query",
            "type": "string",
            "required": true,
            "description": "Query"
          },
          {
            "name": "date_posted",
            "type": "select",
            "required": false,
            "description": "Date posted",
            "options": [
              "all",
              "yesterday",
              "this_week",
              "this_month",
              "last_3_months",
              "last_6_months"
            ]
          },
          {
            "name": "page",
            "type": "string",
            "required": false,
            "description": "Page"
          }
        ]
      },
      {
        "name": "Posts",
        "method": "GET",
        "description": "Posts",
        "path": "/v2/instagram/user/posts",
        "params": [
          {
            "name": "handle",
            "type": "string",
            "required": true,
            "description": "Handle"
          },
          {
            "name": "next_max_id",
            "type": "string",
            "required": false,
            "description": "Next max id"
          },
          {
            "name": "trim",
            "type": "boolean",
            "required": false,
            "description": "Trim"
          }
        ]
      }
    ]
  },
  {
    "id": "account",
    "name": "Account",
    "description": "Account endpoints",
    "endpoints": [
      {
        "name": "Credit Balance",
        "method": "GET",
        "description": "Credit Balance",
        "path": "/v1/account/credit-balance",
        "params": []
      },
      {
        "name": "Get API Usage",
        "method": "GET",
        "description": "Get API Usage",
        "path": "/v1/account/get-api-usage",
        "params": [
          {
            "name": "endpoint",
            "type": "string",
            "required": false,
            "description": "Endpoint"
          },
          {
            "name": "statusCode",
            "type": "string",
            "required": false,
            "description": "Statuscode"
          },
          {
            "name": "page",
            "type": "string",
            "required": false,
            "description": "Page"
          }
        ]
      },
      {
        "name": "Get Daily Usage Count",
        "method": "GET",
        "description": "Get Daily Usage Count",
        "path": "/v1/account/get-daily-usage-count",
        "params": []
      },
      {
        "name": "Get Most Used Routes",
        "method": "GET",
        "description": "Get Most Used Routes",
        "path": "/v1/account/get-most-used-routes",
        "params": [
          {
            "name": "start_time",
            "type": "string",
            "required": false,
            "description": "Start time"
          },
          {
            "name": "end_time",
            "type": "string",
            "required": false,
            "description": "End time"
          }
        ]
      }
    ]
  }
];
