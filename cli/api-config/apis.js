// Endpoint catalog adapted from ScrapeCreators CLI under the MIT license.
// Account endpoints are exported separately because the CLI exposes them through dedicated commands only.
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
            "description": "YouTube channel ID. Can pass a channelId, handle or url",
            "placeholder": "UC-9-kyTW8ZkZNDHQJ6FgpwQ"
          },
          {
            "name": "handle",
            "type": "string",
            "required": false,
            "description": "YouTube channel handle. Can pass a channelId, handle or url",
            "placeholder": "ThePatMcAfeeShow"
          },
          {
            "name": "url",
            "type": "string",
            "required": false,
            "description": "YouTube channel URL. Can pass a channelId, handle or url",
            "placeholder": "https://www.youtube.com/@ThePatMcAfeeShow"
          },
          {
            "name": "cache_max_age",
            "type": "select",
            "required": false,
            "description": "If we have a response in the cache that is this many days old or newer, return the cached response with \"cached\": true and a \"cached_at\" timestamp. Otherwise, scrape a live result.",
            "options": [
              "1d",
              "3d",
              "7d",
              "14d",
              "30d"
            ],
            "placeholder": "7d"
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
            "description": "YouTube channel ID",
            "placeholder": "UC-9-kyTW8ZkZNDHQJ6FgpwQ"
          },
          {
            "name": "handle",
            "type": "string",
            "required": false,
            "description": "YouTube channel handle",
            "placeholder": "ThePatMcAfeeShow"
          },
          {
            "name": "sort",
            "type": "select",
            "required": false,
            "description": "Sort by latest or popular",
            "options": [
              "latest",
              "popular"
            ],
            "placeholder": "latest"
          },
          {
            "name": "continuationToken",
            "type": "string",
            "required": false,
            "description": "Continuation token to get more videos. Get 'continuationToken' from previous response.",
            "placeholder": "4qmFsgKrCBIYVUNkRkpXVWE0M3NtUm00SXBIQnB"
          },
          {
            "name": "is_paid_promotions",
            "type": "string",
            "required": false,
            "description": "Set to 'true' to search YouTube's public paid product placement / sponsorship / endorsement search surface. This returns normal YouTube videos where the creator declared paid promotion. Cannot be combined with filter, uploadDate, sortBy, type, duration, or includeExtras.",
            "placeholder": "false"
          },
          {
            "name": "includeExtras",
            "type": "string",
            "required": false,
            "description": "This will get you the like + comment count and the description. To get the full details of the video, use the /v1/youtube/video endpoint. Honestly, if you use this param, the error rate is higher. We might deprecate this param in the future.",
            "placeholder": "false"
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
            "description": "YouTube channel ID",
            "placeholder": "UCX6OQ3DkcsbYNE6H8uQQuVA"
          },
          {
            "name": "handle",
            "type": "string",
            "required": false,
            "description": "YouTube channel handle",
            "placeholder": "MrBeast"
          },
          {
            "name": "continuationToken",
            "type": "string",
            "required": false,
            "description": "Continuation token to get more community posts. Get 'continuationToken' from previous response.",
            "placeholder": "4qmFsgKrCBIYVUNkRkpXVWE0M3NtUm00SXBIQnB"
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
            "description": "YouTube channel ID",
            "placeholder": "UCWsDFcIhY2DBi3GB5uykGXA"
          },
          {
            "name": "handle",
            "type": "string",
            "required": false,
            "description": "YouTube channel handle",
            "placeholder": "IShowSpeed"
          },
          {
            "name": "continuationToken",
            "type": "string",
            "required": false,
            "description": "Continuation token to get more lives. Get 'continuationToken' from previous response.",
            "placeholder": "4qmFsgKrCBIYVUNkRkpXVWE0M3NtUm00SXBIQnB"
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
            "description": "YouTube channel ID",
            "placeholder": "UCxcTeAKWJca6XyJ37_ZoKIQ"
          },
          {
            "name": "handle",
            "type": "string",
            "required": false,
            "description": "YouTube channel handle",
            "placeholder": "ThePatMcAfeeShow"
          },
          {
            "name": "continuationToken",
            "type": "string",
            "required": false,
            "description": "Continuation token to get more playlists. Get 'continuationToken' from previous response.",
            "placeholder": "4qmFsgKrCBIYVUNkRkpXVWE0M3NtUm00SXBIQnB"
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
            "description": "Can pass channelId or handle",
            "placeholder": "starterstory"
          },
          {
            "name": "channelId",
            "type": "string",
            "required": false,
            "description": "Can pass channelId or handle",
            "placeholder": "UC-9-kyTW8ZkZNDHQJ6FgpwQ"
          },
          {
            "name": "sort",
            "type": "select",
            "required": false,
            "description": "Sort by newest or popular",
            "options": [
              "newest",
              "popular"
            ],
            "placeholder": "newest"
          },
          {
            "name": "continuationToken",
            "type": "string",
            "required": false,
            "description": "Continuation token to get more videos. Get 'continuationToken' from previous response.",
            "placeholder": "4qmFsgKrCBIYVUNkRkpXVWE0M3NtUm00SXBIQnB"
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
            "description": "The URL of the YouTube community post to get",
            "placeholder": "https://www.youtube.com/post/Ugkxvj2KoApYAXoqLWnKVr6zZe5JjeHrQeP8"
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
            "description": "The ID of the YouTube playlist. In the YouTube URL it will be the 'list' parameter.",
            "placeholder": "PLP32wGpgzmIlInfgKVFfCwVsxgGqZNIiS"
          },
          {
            "name": "continuationToken",
            "type": "string",
            "required": false,
            "description": "Token from the previous response to get the next page. Omit for the first page. Stop when the returned continuationToken is null.",
            "placeholder": ""
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
            "description": "Search query. For stricter title matching, use YouTube's intitle: operator, for example intitle:\"Foursquare Swarm\". Quoted queries by themselves may still be broadened by YouTube when no fresh exact matches are available."
          },
          {
            "name": "uploadDate",
            "type": "select",
            "required": false,
            "description": "Upload date",
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
            "description": "Sort by",
            "options": [
              "relevance",
              "popular"
            ],
            "placeholder": "relevance"
          },
          {
            "name": "type",
            "type": "select",
            "required": false,
            "description": "Type of content to search for",
            "options": [
              "videos",
              "shorts",
              "channels",
              "playlists"
            ],
            "placeholder": "videos"
          },
          {
            "name": "duration",
            "type": "select",
            "required": false,
            "description": "Duration of the video. Only applies to videos (not shorts).",
            "options": [
              "under_3_min",
              "between_3_and_20_min",
              "over_20_min"
            ],
            "placeholder": "under_3_min"
          },
          {
            "name": "region",
            "type": "string",
            "required": false,
            "description": "2 letter country code of the country to put the proxy in.",
            "placeholder": "US"
          },
          {
            "name": "continuationToken",
            "type": "string",
            "required": false,
            "description": "Continuation token to get more videos. Get 'continuationToken' from previous response.",
            "placeholder": "4qmFsgKrCBIYVUNkRkpXVWE0M3NtUm00SXBIQnB"
          },
          {
            "name": "includeExtras",
            "type": "string",
            "required": false,
            "description": "This will get you the like + comment count and the description. To get the full details of the video, use the /v1/youtube/video endpoint. *This will slow down the response slightly.*",
            "placeholder": "false"
          },
          {
            "name": "is_paid_promotions",
            "type": "string",
            "required": false,
            "description": "Set to 'true' to search YouTube's public paid product placement, sponsorship, and endorsement surface."
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
            "description": "Hashtag to search for",
            "placeholder": "funny"
          },
          {
            "name": "continuationToken",
            "type": "string",
            "required": false,
            "description": "Continuation token to get more videos. Get 'continuationToken' from previous response.",
            "placeholder": "4qmFsgKrCBIYVUNkRkpXVWE0M3NtUm00SXBIQnB"
          },
          {
            "name": "type",
            "type": "select",
            "required": false,
            "description": "Search for all types of content or only shorts",
            "options": [
              "all",
              "shorts"
            ],
            "placeholder": "all",
            "default": "all"
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
            "description": "Partial or complete YouTube search query",
            "placeholder": "mr beast"
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
            "description": "YouTube video or short URL",
            "placeholder": "https://youtu.be/gTCi7PaOPLY"
          },
          {
            "name": "language",
            "type": "string",
            "required": false,
            "description": "Preferred response language (mapped to Accept-Language header; not guaranteed due to YouTube localization behavior). 2 letter language code, ie 'en', 'es', 'fr' etc.",
            "placeholder": "en"
          },
          {
            "name": "cache_max_age",
            "type": "select",
            "required": false,
            "description": "If we have a response in the cache that is this many days old or newer, return the cached response with \"cached\": true and a \"cached_at\" timestamp. Otherwise, scrape a live result.",
            "options": [
              "1d",
              "3d",
              "7d",
              "14d",
              "30d"
            ],
            "placeholder": "7d"
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
            "description": "Continuation token for the comment replies. Use 'repliesContinuationToken' from the Comments endpoint, or 'continuationToken' from a previous replies response to paginate.",
            "placeholder": "Eg0SC2RRdzR3OVdnWGNRGAYygwEaUBIa..."
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
            "description": "YouTube video URL",
            "placeholder": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
          },
          {
            "name": "continuationToken",
            "type": "string",
            "required": false,
            "description": "Continuation token to get more comments. Get 'continuationToken' from previous response.",
            "placeholder": "4qmFsgKrCBIYVUNkRkpXVWE0M3NtUm00SXBIQnB"
          },
          {
            "name": "order",
            "type": "select",
            "required": false,
            "description": "Order of comments",
            "options": [
              "top",
              "newest"
            ],
            "placeholder": "top"
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
            "description": "YouTube video or short URL",
            "placeholder": "https://www.youtube.com/watch?v=AVO0ifle-OU"
          },
          {
            "name": "language",
            "type": "string",
            "required": false,
            "description": "2 letter language code used for transcript lookup, ie 'en', 'es', 'fr' etc.",
            "placeholder": "en"
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
            "description": "YouTube video or short URL",
            "placeholder": "https://www.youtube.com/watch?v=bjVIDXPP7Uk"
          },
          {
            "name": "language",
            "type": "string",
            "required": false,
            "description": "Language code, ie 'en', 'es', 'fr' or 'en-US'. Overrides the default track selection unless original_audio=true. If omitted, prefers captions matching the original spoken language when YouTube identifies the original audio. If that metadata is unavailable or ambiguous, prefers an auto-generated caption, otherwise the first caption track. If the requested or identified original language has no matching captions, the transcript will be null.",
            "placeholder": "en"
          },
          {
            "name": "original_audio",
            "type": "boolean",
            "required": false,
            "description": "Set to true to return captions only in the original spoken language identified by YouTube. Takes precedence over language. If the original audio cannot be reliably identified or has no matching captions, transcript, transcript_only_text, and language are null. a returned transcript Omit or set to false for the existing default selection.",
            "placeholder": "true"
          },
          {
            "name": "cache_max_age",
            "type": "select",
            "required": false,
            "description": "If we have a response in the cache that is this many days old or newer, return the cached response with \"cached\": true and a \"cached_at\" timestamp. Otherwise, scrape a live result.",
            "options": [
              "1d",
              "3d",
              "7d",
              "14d",
              "30d"
            ],
            "placeholder": "7d"
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
            "description": "Creative Center Top Ads material ID or URL, or a public Ads Library ad ID or library.tiktok.com detail URL.",
            "placeholder": "1869335220395266"
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
            "description": "General ad search. Provide either query or advertiser_name, not both.",
            "placeholder": "The Creator Loop"
          },
          {
            "name": "advertiser_name",
            "type": "string",
            "required": false,
            "description": "Advertiser name to resolve through TikTok's typeahead and search by advertiser entity. Falls back to TikTok's name search when no entity matches. Provide either advertiser_name or query, not both.",
            "placeholder": "Gymshark"
          },
          {
            "name": "adv_biz_ids",
            "type": "string",
            "required": false,
            "description": "TikTok advertiser business ID from a See all ads link. Use it with advertiser_name to pin the exact advertiser. Required companion: advertiser_name; ID-only searches return 400 because TikTok ignores the ID without the name.",
            "placeholder": "7078923208527618049"
          },
          {
            "name": "cursor",
            "type": "string",
            "required": false,
            "description": "Opaque cursor returned from the previous response.",
            "placeholder": "cursor from previous response"
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
            "description": "Public TikTok collection URL",
            "placeholder": "https://www.tiktok.com/@kibblemaster808/collection/Want-to-go-7665668414573546258"
          },
          {
            "name": "cursor",
            "type": "string",
            "required": false,
            "description": "Cursor to get more videos. Use max_cursor from the previous response.",
            "placeholder": "4"
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
            "description": "Page number",
            "placeholder": 1
          },
          {
            "name": "sortBy",
            "type": "select",
            "required": false,
            "description": "Sort creators by engagement, follower count, or average views",
            "options": [
              "engagement",
              "follower",
              "avg_views"
            ],
            "placeholder": "engagement"
          },
          {
            "name": "followerCount",
            "type": "select",
            "required": false,
            "description": "Filter by follower count range",
            "options": [
              "10K-100K",
              "100K-1M",
              "1M-10M",
              "10M+"
            ],
            "placeholder": "10K-100K"
          },
          {
            "name": "creatorCountry",
            "type": "string",
            "required": false,
            "description": "Country code of the creator",
            "placeholder": "US",
            "options": [
              "AU",
              "BR",
              "CA",
              "EG",
              "FR",
              "DE",
              "ID",
              "IL",
              "IT",
              "JP",
              "MY",
              "PH",
              "RU",
              "SA",
              "SG",
              "KR",
              "ES",
              "TW",
              "TH",
              "TR",
              "AE",
              "GB",
              "US",
              "VN"
            ]
          },
          {
            "name": "audienceCountry",
            "type": "string",
            "required": false,
            "description": "Country code of the audience/follower",
            "placeholder": "US",
            "options": [
              "AU",
              "BR",
              "CA",
              "EG",
              "FR",
              "DE",
              "ID",
              "IL",
              "IT",
              "JP",
              "MY",
              "PH",
              "RU",
              "SA",
              "SG",
              "KR",
              "ES",
              "TW",
              "TH",
              "TR",
              "AE",
              "GB",
              "US",
              "VN"
            ]
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
            "description": "Where you want the proxy to be. This doesn't mean that you will only see TikToks from this region, you will just see the content that isn't banned in that region.",
            "placeholder": "US"
          },
          {
            "name": "trim",
            "type": "boolean",
            "required": false,
            "description": "Set to true to get a trimmed response.",
            "placeholder": "false"
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
            "description": "TikTok live room id. Get this from `/v1/tiktok/user/live` in `liveRoomUserInfo.roomId` or `liveRoom.id` when the user is live.",
            "placeholder": "7523685855395842871"
          },
          {
            "name": "user_id",
            "type": "string",
            "required": true,
            "description": "TikTok numeric user id for the live owner. Get this from `/v1/tiktok/profile` in `user.id`.",
            "placeholder": "6742945285876515845"
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
            "description": "The URL of the product to get details for.",
            "placeholder": "https://www.tiktok.com/shop/pdp/goli-ashwagandha-gummies-with-vitamin-d-ksm-66-vegan-non-gmo/1729587769570529799"
          },
          {
            "name": "region",
            "type": "string",
            "required": false,
            "description": "Region for the product details request. US is the reliable region right now; non-US regions should not be considered reliable and may return `bad_request` or missing product data. Sorry for the inconvenience.",
            "placeholder": "US"
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
            "description": "TikTok handle. You can pass handle or user_id.",
            "placeholder": "stoolpresidente"
          },
          {
            "name": "user_id",
            "type": "string",
            "required": false,
            "description": "TikTok user id.",
            "placeholder": "6659752019493208069"
          },
          {
            "name": "cache_max_age",
            "type": "select",
            "required": false,
            "description": "If we have a response in the cache that is this many days old or newer, return the cached response with \"cached\": true and a \"cached_at\" timestamp. Otherwise, scrape a live result.",
            "options": [
              "1d",
              "3d",
              "7d",
              "14d",
              "30d"
            ],
            "placeholder": "7d"
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
            "description": "TikTok handle",
            "placeholder": "stoolpresidente"
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
            "description": "Hashtag to search for (without #)"
          },
          {
            "name": "region",
            "type": "string",
            "required": false,
            "description": "Region the proxy will be set to. Note: this isn't going to grab you all tiktoks from this region, you're just setting the proxy there.",
            "placeholder": "US"
          },
          {
            "name": "cursor",
            "type": "string",
            "required": false,
            "description": "Cursor to get more videos. Get 'cursor' from previous response.",
            "placeholder": 10
          },
          {
            "name": "trim",
            "type": "boolean",
            "required": false,
            "description": "Set to true to get a trimmed response",
            "placeholder": false
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
            "description": "Keyword to search for"
          },
          {
            "name": "date_posted",
            "type": "select",
            "required": false,
            "description": "Time Frame",
            "options": [
              "yesterday",
              "this-week",
              "this-month",
              "last-3-months",
              "last-6-months",
              "all-time"
            ],
            "placeholder": "all-time"
          },
          {
            "name": "sort_by",
            "type": "select",
            "required": false,
            "description": "Sort by",
            "options": [
              "relevance",
              "most-liked",
              "date-posted"
            ],
            "placeholder": "relevance"
          },
          {
            "name": "region",
            "type": "string",
            "required": false,
            "description": "Note, this doesn't filter the tiktoks only in a specfic region, it puts the proxy there. Use it in case you want to scrape posts only available for some country. Use 2 letter country codes like US, GB, FR, etc",
            "placeholder": null
          },
          {
            "name": "cursor",
            "type": "string",
            "required": false,
            "description": "Cursor to get more videos. Get 'cursor' from previous response.",
            "placeholder": 10
          },
          {
            "name": "trim",
            "type": "boolean",
            "required": false,
            "description": "Set to true to get a trimmed response",
            "placeholder": false
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
            "description": "Search query to get suggestions for",
            "placeholder": "dogs"
          },
          {
            "name": "region",
            "type": "string",
            "required": false,
            "description": "Region code for suggestions",
            "placeholder": "US"
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
            "description": "Keyword to search for",
            "placeholder": "funny"
          },
          {
            "name": "publish_time",
            "type": "select",
            "required": false,
            "description": "Time Frame TikTok was posted",
            "options": [
              "yesterday",
              "this-week",
              "this-month",
              "last-3-months",
              "last-6-months",
              "all-time"
            ],
            "placeholder": "all-time"
          },
          {
            "name": "sort_by",
            "type": "select",
            "required": false,
            "description": "Sort by",
            "options": [
              "relevance",
              "most-liked",
              "date-posted"
            ],
            "placeholder": "relevance"
          },
          {
            "name": "region",
            "type": "string",
            "required": false,
            "description": "Note, this doesn't filter the tiktoks only in a specfic region, it puts the proxy there. Use it in case you want to scrape posts only available for some country. Use 2 letter country codes like US, GB, FR, etc",
            "placeholder": null
          },
          {
            "name": "cursor",
            "type": "string",
            "required": false,
            "description": "Cursor to get more videos. Get 'cursor' from previous response.",
            "placeholder": 0
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
            "description": "Search query for users"
          },
          {
            "name": "cursor",
            "type": "string",
            "required": false,
            "description": "Cursor to get more users. Get 'cursor' from previous response.",
            "placeholder": 10
          },
          {
            "name": "trim",
            "type": "boolean",
            "required": false,
            "description": "Set to true to get a trimmed response",
            "placeholder": false
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
            "description": "The URL of the product (required if product_id is not provided)",
            "placeholder": "https://www.tiktok.com/shop/pdp/cat-nail-clipper-by-potaroma-adjustable-sizes-built-in-file-safe-for-kittens-cats/1731578642912612516"
          },
          {
            "name": "product_id",
            "type": "string",
            "required": false,
            "description": "The ID of the product (required if url is not provided)",
            "placeholder": "1731578642912612516"
          },
          {
            "name": "region",
            "type": "string",
            "required": false,
            "description": "The region of the product. US is the reliable region right now; non-US regions should not be considered reliable and may return limited or inconsistent review data. Sorry for the inconvenience.",
            "placeholder": "US"
          },
          {
            "name": "page",
            "type": "string",
            "required": false,
            "description": "The page number of the reviews",
            "placeholder": "1"
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
            "description": "The TikTok Shop store URL.",
            "placeholder": "https://www.tiktok.com/shop/store/goli-nutrition/7495794203056835079"
          },
          {
            "name": "cursor",
            "type": "string",
            "required": false,
            "description": "Cursor parameter from the previous response to retrieve the next page of products. Omit for the first page.",
            "placeholder": ""
          },
          {
            "name": "sort_by",
            "type": "select",
            "required": false,
            "description": "Sort products by best-selling items (`top`) or newest products (`new_releases`). Defaults to `top`.",
            "options": [
              "top",
              "new_releases"
            ],
            "placeholder": "top"
          },
          {
            "name": "region",
            "type": "string",
            "required": false,
            "description": "Region to get shop products from. Defaults to US if not provided. Non-US regions are not reliable right now and may return `not_found` or limited catalog data even when the shop appears in search. Sorry for the inconvenience.",
            "placeholder": "US",
            "options": [
              "US",
              "GB",
              "DE",
              "FR",
              "IT",
              "ID",
              "MY",
              "MX",
              "PH",
              "SG",
              "ES",
              "TH",
              "VN",
              "BR",
              "JP",
              "IE"
            ]
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
            "description": "Term you want to search for",
            "placeholder": "shoes"
          },
          {
            "name": "page",
            "type": "string",
            "required": false,
            "description": "Page number to retrieve",
            "placeholder": "1"
          },
          {
            "name": "region",
            "type": "string",
            "required": false,
            "description": "Region to search shop products in. Non-US TikTok Shop regions are not reliable right now and may return limited or inconsistent results. Sorry for the inconvenience.",
            "placeholder": "US",
            "options": [
              "US",
              "GB",
              "DE",
              "FR",
              "IT",
              "ID",
              "MY",
              "MX",
              "PH",
              "SG",
              "ES",
              "TH",
              "VN",
              "BR",
              "JP",
              "IE"
            ]
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
            "description": "This is a little confusing because this isn't songId like you'd think. It is the clipId. I guess because you can clip different portions of a song 🤷‍♂️",
            "placeholder": "7439295283975702544"
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
            "description": "This is clipId. Can be found on a url like so: https://www.tiktok.com/music/That%27s-Who-I-Praise-7370375686554782506, where 7370375686554782506 is the clipId",
            "placeholder": "7439295283975702544"
          },
          {
            "name": "cursor",
            "type": "string",
            "required": false,
            "description": "The cursor to get the next page of results.",
            "placeholder": 12
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
            "description": "TikTok handle",
            "placeholder": "shakira"
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
            "description": "TikTok handle",
            "placeholder": "stoolpresidente"
          },
          {
            "name": "user_id",
            "type": "string",
            "required": false,
            "description": "User id. Use this for faster response times.",
            "placeholder": "6659752019493208069"
          },
          {
            "name": "min_time",
            "type": "string",
            "required": false,
            "description": "Used to paginate. Get 'min_time' from previous response.",
            "placeholder": 1605663376
          },
          {
            "name": "trim",
            "type": "boolean",
            "required": false,
            "description": "Set to true to get a trimmed response",
            "placeholder": false
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
            "description": "TikTok handle",
            "placeholder": "stoolpresidente"
          },
          {
            "name": "min_time",
            "type": "string",
            "required": false,
            "description": "Used to paginate. Get 'min_time' from previous response.",
            "placeholder": 1605663376
          },
          {
            "name": "trim",
            "type": "boolean",
            "required": false,
            "description": "Set to true to get a trimmed response",
            "placeholder": false
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
            "description": "TikTok handle",
            "placeholder": "thejustalex"
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
            "description": "The handle of the user",
            "placeholder": "mrtiktokreviews"
          },
          {
            "name": "region",
            "type": "string",
            "required": false,
            "description": "Region to put the proxy in. Non-US TikTok Shop regions are not reliable right now and may return limited or inconsistent showcase data. Sorry for the inconvenience.",
            "placeholder": "US"
          },
          {
            "name": "cursor",
            "type": "string",
            "required": false,
            "description": "The cursor to the next page of products",
            "placeholder": "21"
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
            "description": "TikTok comment ID. This is the cid from the comments endpoint.",
            "placeholder": "7623828115408274207"
          },
          {
            "name": "url",
            "type": "string",
            "required": true,
            "description": "TikTok video URL. This is the url from the comments endpoint.",
            "placeholder": "https://www.tiktok.com/@stoolpresidente/video/7623818255903329566"
          },
          {
            "name": "cursor",
            "type": "string",
            "required": false,
            "description": "Cursor to get more replies. Get 'cursor' from previous response.",
            "placeholder": 3
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
            "description": "TikTok video URL"
          },
          {
            "name": "cursor",
            "type": "string",
            "required": false,
            "description": "Cursor to get more comments. Get 'cursor' from previous response.",
            "placeholder": 20
          },
          {
            "name": "trim",
            "type": "boolean",
            "required": false,
            "description": "Set to true to get a trimmed response",
            "placeholder": false
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
            "description": "Optional opt-in, enabled only by the literal query value 'true' (allow_long_transcription=true). Defaults to false when omitted; other values do not enable long-transcription mode. By default, successful AI transcription of videos up to 120 seconds (two minutes, inclusive) Over 120 seconds, videos cannot be AI-transcribed by default. Set allow_long_transcription=true to opt into AI with no fixed duration cap: ALL successful AI transcriptions in this mode For example, 177 seconds Empty AI results and errors This flag does not force AI and does not enable transcription or AI fallback by itself. Keep get_transcript=true or use_ai_as_fallback=true where required by the endpoint. Returned native captions still These routes remain synchronous. Allow at least 30 seconds on your client. Long transcription can exceed resource limits or timeouts, so successful transcription of long videos is not guaranteed.",
            "placeholder": false,
            "default": false
          },
          {
            "name": "url",
            "type": "string",
            "required": true,
            "description": "TikTok video URL",
            "placeholder": "https://www.tiktok.com/@stoolpresidente/video/7499229683859426602"
          },
          {
            "name": "language",
            "type": "string",
            "required": false,
            "description": "Language of the transcript. 2 letter language code, ie 'en', 'es', 'fr', 'de', 'it', 'ja', 'ko', 'zh'",
            "placeholder": "en"
          },
          {
            "name": "use_ai_as_fallback",
            "type": "boolean",
            "required": false,
            "description": "Set to 'true' to use AI only when existing captions are unavailable. Defaults to false. By default, successful AI transcription of videos up to 120 seconds (two minutes, inclusive) Over 120 seconds, videos cannot be AI-transcribed by default. Set allow_long_transcription=true to opt into AI with no fixed duration cap: ALL successful AI transcriptions in this mode For example, 177 seconds Empty AI results and errors These routes remain synchronous. Allow at least 30 seconds on your client. Long transcription can exceed resource limits or timeouts, so successful transcription of long videos is not guaranteed.",
            "placeholder": "false"
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
            "description": "TikTok video or photo/slideshow URL",
            "placeholder": "https://www.tiktok.com/@randomspamvideos25/video/7251387037834595630"
          },
          {
            "name": "get_transcript",
            "type": "boolean",
            "required": false,
            "description": "Native captions are opt-in via get_transcript=true and are included in the existing base endpoint price. This detail endpoint does not use AI transcription or an AI fallback; missing captions return no transcript. For AI fallback, use /v1/tiktok/video/transcript.",
            "placeholder": false
          },
          {
            "name": "region",
            "type": "string",
            "required": false,
            "description": "Two-letter country code passed to the primary post source. Defaults to US. Try the creator's country if a post is unavailable. This does not guarantee a matching browser/proxy country or complete retrieval; the fallback source does not receive this parameter.",
            "placeholder": "US"
          },
          {
            "name": "trim",
            "type": "boolean",
            "required": false,
            "description": "Set to true to get a trimmed response",
            "placeholder": false
          },
          {
            "name": "download_media",
            "type": "boolean",
            "required": false,
            "description": "Set to true to download the video/images and get back permanent Supabase URLs.",
            "placeholder": false
          },
          {
            "name": "cache_max_age",
            "type": "select",
            "required": false,
            "description": "If we have a response in the cache that is this many days old or newer, return the cached response with \"cached\": true and a \"cached_at\" timestamp. Otherwise, scrape a live result.",
            "options": [
              "1d",
              "3d",
              "7d",
              "14d",
              "30d"
            ],
            "placeholder": "7d"
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
            "description": "TikTok handle",
            "placeholder": "stoolpresidente"
          },
          {
            "name": "user_id",
            "type": "string",
            "required": false,
            "description": "TikTok user id. Use this for faster responses.",
            "placeholder": "6659752019493208069"
          },
          {
            "name": "sort_by",
            "type": "select",
            "required": false,
            "description": "What to sort by",
            "options": [
              "latest",
              "popular"
            ],
            "placeholder": "latest"
          },
          {
            "name": "max_cursor",
            "type": "string",
            "required": false,
            "description": "Cursor to get more videos. Get 'max_cursor' from previous response.",
            "placeholder": "1734562353000"
          },
          {
            "name": "region",
            "type": "string",
            "required": false,
            "description": "Country code passed to the profile-feed source. Defaults to US. If a profile should have videos but returns none, try another relevant two-letter country code. Changing this value does not guarantee that region-restricted posts will appear.",
            "placeholder": "US"
          },
          {
            "name": "include_pinned",
            "type": "boolean",
            "required": false,
            "description": "Whether to include pinned posts. Defaults to true. Set to false to exclude posts marked as pinned, including with trim=true. Keep this value unchanged while paginating. Filtering can produce shorter or empty pages; follow has_more and max_cursor. Successful pages still",
            "placeholder": "true"
          },
          {
            "name": "trim",
            "type": "boolean",
            "required": false,
            "description": "Set to true for a trimmed down version of the response",
            "placeholder": "false"
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
            "description": "The audio id from the Instagram audio page URL.",
            "placeholder": "1392969992841787"
          },
          {
            "name": "cursor",
            "type": "string",
            "required": false,
            "description": "Pagination cursor returned by Instagram from the previous response.",
            "placeholder": "Gsbyq-aju4eF02y..."
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
            "description": "Instagram user id",
            "placeholder": "314216"
          },
          {
            "name": "cache_max_age",
            "type": "select",
            "required": false,
            "description": "If we have a response in the cache that is this many days old or newer, return the cached response with \"cached\": true and a \"cached_at\" timestamp. Otherwise, scrape a live result.",
            "options": [
              "1d",
              "3d",
              "7d",
              "14d",
              "30d"
            ],
            "placeholder": "7d"
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
            "description": "Instagram post or reel URL"
          },
          {
            "name": "region",
            "type": "string",
            "required": false,
            "description": "2 letter country code to set the proxy in",
            "placeholder": "US"
          },
          {
            "name": "trim",
            "type": "boolean",
            "required": false,
            "description": "Set to true to get a trimmed response",
            "placeholder": false
          },
          {
            "name": "download_media",
            "type": "boolean",
            "required": false,
            "description": "Set to true to download the video/images and get back permanent Supabase URLs.",
            "placeholder": false
          },
          {
            "name": "include_play_count",
            "type": "boolean",
            "required": false,
            "description": "Set to false to omit `video_play_count` and skip its additional fetch for a faster response. Defaults to true.",
            "placeholder": true
          },
          {
            "name": "cache_max_age",
            "type": "select",
            "required": false,
            "description": "If we have a response in the cache that is this many days old or newer, return the cached response with \"cached\": true and a \"cached_at\" timestamp. Otherwise, scrape a live result.",
            "options": [
              "1d",
              "3d",
              "7d",
              "14d",
              "30d"
            ],
            "placeholder": "7d"
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
            "description": "The Instagram post or reel URL",
            "placeholder": "https://www.instagram.com/reel/C8rKmYvsrck"
          },
          {
            "name": "comment_id",
            "type": "string",
            "required": true,
            "description": "The parent comment ID from the Comments endpoint",
            "placeholder": "18038110327814211"
          },
          {
            "name": "cursor",
            "type": "string",
            "required": false,
            "description": "The cursor to get more replies. Get `cursor` from the previous response.",
            "placeholder": "QVFDc29..."
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
            "description": "Instagram handle",
            "placeholder": "jane"
          },
          {
            "name": "trim",
            "type": "boolean",
            "required": false,
            "description": "Set to true to get a trimmed response",
            "placeholder": false
          },
          {
            "name": "cache_max_age",
            "type": "select",
            "required": false,
            "description": "If we have a response in the cache that is this many days old or newer, return the cached response with \"cached\": true and a \"cached_at\" timestamp. Otherwise, scrape a live result.",
            "options": [
              "1d",
              "3d",
              "7d",
              "14d",
              "30d"
            ],
            "placeholder": "7d"
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
            "description": "Instagram handle",
            "placeholder": "lifestyleperfume"
          },
          {
            "name": "allow_estimated",
            "type": "boolean",
            "required": false,
            "description": "Set to true to return scaled estimates when Instagram abbreviates counts for profiles with more than 10,000 posts. Defaults to false; false or omitted returns an uncharged 422 when only an estimate is available.",
            "placeholder": false
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
            "description": "The username, hashtag, place, or keyword to search for.",
            "placeholder": "nike"
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
            "description": "The hashtag to search for. Include or omit the #.",
            "placeholder": "makeup"
          },
          {
            "name": "date_posted",
            "type": "select",
            "required": false,
            "description": "Only return Google-indexed posts found in this relative window.",
            "options": [
              "last-hour",
              "last-day",
              "last-week",
              "last-month",
              "last-year"
            ],
            "placeholder": "last-week"
          },
          {
            "name": "media_type",
            "type": "select",
            "required": false,
            "description": "Use all to search public posts and reels, or reels to only return reels. Defaults to all.",
            "options": [
              "all",
              "reels"
            ],
            "placeholder": "all"
          },
          {
            "name": "cursor",
            "type": "string",
            "required": false,
            "description": "The cursor returned by the previous response. It is the next Google results page number and cannot exceed 11; cursor 12 or greater returns a 400 response.",
            "placeholder": "2"
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
            "description": "The Popular topic to search for.",
            "placeholder": "basketball"
          },
          {
            "name": "cursor",
            "type": "string",
            "required": false,
            "description": "The opaque cursor returned by the previous response. Use it with the same query to fetch the next page of posts.",
            "placeholder": "opaque-cursor-from-response"
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
            "description": "The profile name or username to search for.",
            "placeholder": "fitness coach"
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
            "description": "Instagram handle",
            "placeholder": "jane"
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
            "description": "The ID of the highlight to get details for",
            "placeholder": "18067016518767507"
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
            "description": "Instagram user id. Use for faster response times.",
            "placeholder": "2700692569"
          },
          {
            "name": "handle",
            "type": "string",
            "required": false,
            "description": "Instagram handle. Use user_id for faster response times.",
            "placeholder": "jane"
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
            "description": "Instagram user id. Use this for faster response times.",
            "placeholder": "2700692569"
          },
          {
            "name": "handle",
            "type": "string",
            "required": false,
            "description": "Instagram handle. Use user_id for faster response times.",
            "placeholder": "jane"
          },
          {
            "name": "max_id",
            "type": "string",
            "required": false,
            "description": "Max id to get more reels. Get 'max_id' from previous response.",
            "placeholder": "QVFCVzNnS2lI...=="
          },
          {
            "name": "trim",
            "type": "boolean",
            "required": false,
            "description": "Set to true for a trimmed down version of the response",
            "placeholder": "false"
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
            "description": "Numeric Instagram user ID.",
            "placeholder": "325734299"
          },
          {
            "name": "cursor",
            "type": "string",
            "required": false,
            "description": "Cursor returned by the previous response.",
            "placeholder": "3953374600788032611"
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
            "description": "Optional opt-in, enabled only by the literal query value 'true' (allow_long_transcription=true). Defaults to false when omitted; other values do not enable long-transcription mode. By default, successful AI transcription of videos up to 120 seconds (two minutes, inclusive) Over 120 seconds, videos cannot be AI-transcribed by default. Set allow_long_transcription=true to opt into AI with no fixed duration cap: ALL successful AI transcriptions in this mode For example, 177 seconds Empty AI results and errors This flag does not force AI and does not enable transcription or AI fallback by itself. Keep get_transcript=true or use_ai_as_fallback=true where required by the endpoint. Returned native captions still These routes remain synchronous. Allow at least 30 seconds on your client. Long transcription can exceed resource limits or timeouts, so successful transcription of long videos is not guaranteed.",
            "placeholder": false,
            "default": false
          },
          {
            "name": "url",
            "type": "string",
            "required": true,
            "description": "Instagram post or reel URL"
          },
          {
            "name": "cache_max_age",
            "type": "select",
            "required": false,
            "description": "If we have a response in the cache that is this many days old or newer, return the cached response with \"cached\": true and a \"cached_at\" timestamp. Otherwise, scrape a live result (By default, successful AI up to 120 seconds",
            "options": [
              "1d",
              "3d",
              "7d",
              "14d",
              "30d"
            ],
            "placeholder": "7d"
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
            "description": "The URL of the post or reel to get comments from",
            "placeholder": "https://www.instagram.com/reel/DOq6eV6iIgD"
          },
          {
            "name": "cursor",
            "type": "string",
            "required": false,
            "description": "The cursor to get more comments. Get 'cursor' from previous response.",
            "placeholder": "eyJjYWNoZWRfY29tbWVud..."
          },
          {
            "name": "include_replies",
            "type": "boolean",
            "required": false,
            "description": "Set to true to include replies for every returned comment. This is much slower and may time out at 29 seconds.",
            "placeholder": "true"
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
            "description": "The keyword to search for",
            "placeholder": "dogs"
          },
          {
            "name": "date_posted",
            "type": "select",
            "required": false,
            "description": "Google-indexed date window. Recent hour/day filters are not supported because Google does not index Instagram reels reliably enough in those windows.",
            "options": [
              "last-week",
              "last-month",
              "last-year"
            ],
            "placeholder": "last-week"
          },
          {
            "name": "page",
            "type": "string",
            "required": false,
            "description": "The page number to return. Must be between 1 and 11; page 12 or greater returns a 400 response.",
            "placeholder": 1
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
            "description": "Instagram handle"
          },
          {
            "name": "next_max_id",
            "type": "string",
            "required": false,
            "description": "Cursor to get next page of results."
          },
          {
            "name": "trim",
            "type": "boolean",
            "required": false,
            "description": "Set to true to get a trimmed response",
            "placeholder": false
          }
        ]
      }
    ]
  }
];

export const accountApis = [
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
