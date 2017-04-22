#include "stdafx.h"
#include "PluginPushKLData.h"
#include "PluginQuoteServer.h"
#include "Protocol/ProtoPushKLData.h"

#ifdef _DEBUG
#define new DEBUG_NEW
#endif

#define TIMER_ID_CLEAR_CACHE		354
#define TIMER_ID_HANDLE_TIMEOUT_REQ	355

#define EVENT_ID_ACK_REQUEST	368

//tomodify 2
#define PROTO_ID_QUOTE		PROTO_ID_PUSH_KLDATA
#define QUOTE_SERVER_TYPE	QuoteServer_PushKLData
typedef CProtoPushKLData		CProtoQuote;


//////////////////////////////////////////////////////////////////////////

CPluginPushKLData::CPluginPushKLData()
{	
	m_pQuoteData = NULL;
	m_pQuoteServer = NULL;
}

CPluginPushKLData::~CPluginPushKLData()
{
	Uninit();
}

void CPluginPushKLData::Init(CPluginQuoteServer* pQuoteServer, IFTQuoteData*  pQuoteData)
{
	if ( m_pQuoteServer != NULL )
		return;

	if ( pQuoteServer == NULL || pQuoteData == NULL )
	{
		ASSERT(false);
		return;
	}

	m_pQuoteServer = pQuoteServer;
	m_pQuoteData = pQuoteData;
}

void CPluginPushKLData::Uninit()
{
	if ( m_pQuoteServer != NULL )
	{
		m_pQuoteServer = NULL;
		m_pQuoteData = NULL;
	}
}

void CPluginPushKLData::PushStockData(INT64 nStockID, SOCKET sock, StockSubType eStockSubType)
{
	int nKLType = 0;
	switch(eStockSubType)
	{
	case StockSubType_KL_MIN1:
		{
			nKLType = 1;
		}
		break;
	case StockSubType_KL_DAY:
		{
			nKLType = 2;
		}
		break;
	case StockSubType_KL_WEEK:
		{
			nKLType = 3;
		}
		break;
	case StockSubType_KL_MONTH:
		{
			nKLType = 4;
		}
		break;
	case StockSubType_KL_MIN5:
		{
			nKLType = 6;
		}
		break;
	case StockSubType_KL_MIN15:
		{
			nKLType = 7;
		}
		break;
	case StockSubType_KL_MIN30:
		{
			nKLType = 8;
		}
		break;
	case StockSubType_KL_MIN60:
		{
			nKLType = 9;
		}
		break;
	default:
		CHECK_OP(false, NOOP);
		break;
	}

	if (m_pQuoteData->IsKLDataExist(nStockID, nKLType) )
	{
		Quote_StockKLData* pQuoteKL = NULL;
		int nCount = 0;
		DWORD dwLastTime = GetLastPushKL(nStockID, sock, eStockSubType);
		if (m_pQuoteData->FillKLData(nStockID, pQuoteKL, nCount, nKLType, 0))
		{
			QuoteAckDataBody ackbody;
			ackbody.nKLType = nKLType;
			ackbody.nRehabType = 0;
			ackbody.vtKLData.clear();
			DWORD dwMaxTime = dwLastTime;
			for ( int n = 0; n < nCount; n++ )
			{
				PushKLDataAckItem item;
				if (pQuoteKL[n].dwTime < dwLastTime)
				{
					continue;
				}
				if (pQuoteKL[n].dwTime > dwMaxTime)
				{
					dwMaxTime = pQuoteKL[n].dwTime;
				}
				wchar_t szTime[64] = {}; 
				m_pQuoteData->TimeStampToStr(nStockID, pQuoteKL[n].dwTime,szTime);
				item.strTime = szTime;
				item.nOpenPrice = pQuoteKL[n].nOpenPrice;
				item.nClosePrice = pQuoteKL[n].nClosePrice;
				item.nHighestPrice = pQuoteKL[n].nHighestPrice;
				item.nLowestPrice = pQuoteKL[n].nLowestPrice;
				item.nPERatio= pQuoteKL[n].nPERatio;
			    item.nTurnoverRate = pQuoteKL[n].nTurnoverRate;
				item.ddwTDVol= pQuoteKL[n].ddwTDVol;
				item.ddwTDVal = pQuoteKL[n].ddwTDVal;
				ackbody.vtKLData.push_back(item);
			}
			SetLastPushKL(nStockID, sock, eStockSubType, dwMaxTime);
			m_pQuoteData->DeleteKLDataPointer(pQuoteKL);
			
			if ( ackbody.vtKLData.size() > 50 )
			{
				std::reverse(ackbody.vtKLData.begin(), ackbody.vtKLData.end());
				ackbody.vtKLData.resize(1);
			}

			CProtoQuote::ProtoAckDataType ack;
			ack.head.nProtoID = PROTO_ID_PUSH_KLDATA;
			ack.head.ddwErrCode = 0;
			ack.head.nProtoVer = 1;
			ack.body = ackbody;

			StockMktType eMkt = StockMkt_HK;
			wchar_t szStockCode[16] = {}, szStockName[128] = { 0 };
			m_pQuoteData->GetStockInfoByHashVal(nStockID, eMkt, szStockCode, szStockName);
			ack.body.nStockMarket = (int)eMkt;
			CA::Unicode2UTF(szStockCode, ack.body.strStockCode);

			if (ack.body.vtKLData.size() > 0)
			{
				CProtoQuote proto;
				proto.SetProtoData_Ack(&ack);

				Json::Value jsnAck;
				if (proto.MakeJson_Ack(jsnAck))
				{
					std::string strOut;
					CProtoParseBase::ConvJson2String(jsnAck, strOut, true);
					m_pQuoteServer->ReplyQuoteReq(PROTO_ID_PUSH_KLDATA, strOut.c_str(), (int)strOut.size(), sock);
				}
				else
				{
					CHECK_OP(false, NOOP);
				}
			}
		}
		m_pQuoteData->CheckRemoveQuoteKL(nStockID, nKLType);
	}
}

DWORD CPluginPushKLData::GetLastPushKL(INT64 ddwStockHash, SOCKET sock, StockSubType eStockSubType)
{
	std::vector<Stock_PushInfo> &vtInfoSingleSocket = m_mapPushInfo[sock];
	for (std::vector<Stock_PushInfo>::iterator iter_find = vtInfoSingleSocket.begin(); iter_find != vtInfoSingleSocket.end(); iter_find++)
	{
		if (iter_find->ddwStockID == ddwStockHash)
		{
			switch (eStockSubType)
			{
			case StockSubType_KL_DAY:
			{
				return iter_find->dwKLTime_DAY;
			}
			break;
			case StockSubType_KL_MIN5:
			{
				return iter_find->dwKLTime_5MIN;
			}
			break;
			case StockSubType_KL_MIN15:
			{
				return iter_find->dwKLTime_15MIN;
			}
			break;
			case StockSubType_KL_MIN30:
			{
				return iter_find->dwKLTime_30MIN;
			}
			break;
			case StockSubType_KL_MIN60:
			{
				return iter_find->dwKLTime_60MIN;
			}
			break;
			case StockSubType_KL_MIN1:
			{
				return iter_find->dwKLTime_1MIN;
			}
			break;
			case StockSubType_KL_WEEK:
			{
				return iter_find->dwKLTime_WEEK;
			}
			break;
			case StockSubType_KL_MONTH:
			{
				return iter_find->dwKLTime_MONTH;
			}
			break;
			default:
				CHECK_RET(false, 0);
				break;
			}
		}
	}
	Stock_PushInfo NewItem;
	NewItem.ddwStockID = ddwStockHash;
	vtInfoSingleSocket.push_back(NewItem);
	return 0;
}

bool CPluginPushKLData::SetLastPushKL(INT64 ddwStockHash, SOCKET sock, StockSubType eStockSubType, DWORD dwTime)
{
	std::vector<Stock_PushInfo> &vtInfoSingleSocket = m_mapPushInfo[sock];
	for (std::vector<Stock_PushInfo>::iterator iter_find = vtInfoSingleSocket.begin(); iter_find != vtInfoSingleSocket.end(); iter_find++)
	{
		if (iter_find->ddwStockID == ddwStockHash)
		{
			switch (eStockSubType)
			{
			case StockSubType_KL_DAY:
			{
				iter_find->dwKLTime_DAY = dwTime;
			}
			break;
			case StockSubType_KL_MIN5:
			{
				iter_find->dwKLTime_5MIN = dwTime;
			}
			break;
			case StockSubType_KL_MIN15:
			{
				iter_find->dwKLTime_15MIN = dwTime;
			}
			break;
			case StockSubType_KL_MIN30:
			{
				iter_find->dwKLTime_30MIN = dwTime;
			}
			break;
			case StockSubType_KL_MIN60:
			{
				iter_find->dwKLTime_60MIN = dwTime;
			}
			break;
			case StockSubType_KL_MIN1:
			{
				iter_find->dwKLTime_1MIN = dwTime;
			}
			break;
			case StockSubType_KL_WEEK:
			{
				iter_find->dwKLTime_WEEK = dwTime;
			}
			break;
			case StockSubType_KL_MONTH:
			{
				iter_find->dwKLTime_MONTH = dwTime;
			}
			break;
			default:
				CHECK_RET(false, false);
				break;
			}
			return true;
		}
	}
	return false;
}